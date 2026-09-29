using System.Diagnostics;
using System.Text.Json;
using Steamworks;

namespace NightreignP2PHelper;

internal sealed record IdentityReply(string Tag, bool Ok);

// Two owned HTML surfaces keep verification navigations from aborting a pending
// mutation. No cookies, session IDs, HTML, authentication URLs or names leave them.
internal sealed class IdentityHtmlSession : IDisposable
{
    private readonly string account, target;
    private readonly Action tick;
    private readonly List<IDisposable> callbacks = new();
    private readonly Dictionary<string, IdentityReply> replies = new();
    private readonly HashSet<string> actionTokens = new();
    private HHTMLBrowser control, verifier;
    private string inspectionToken = "";
    private string blockToken = "";
    private IdentityPage? inspected;
    private long inspectAt;
    private readonly Stopwatch clock = Stopwatch.StartNew();
    private bool initialized;
    internal bool BlockSettled { get; private set; }
    internal string Profile => "https://steamcommunity.com/profiles/" + target + "/";

    internal IdentityHtmlSession(string account, string target, Action tick)
    {
        this.account = account; this.target = target; this.tick = tick;
        if (!SteamHTMLSurface.Init()) throw new InvalidOperationException("Steam 网页组件暂不可用");
        initialized = true;
        try
        {
            callbacks.Add(Callback<HTML_StartRequest_t>.Create(x => {
                if (Owns(x.unBrowserHandle)) SteamHTMLSurface.AllowStartRequest(x.unBrowserHandle, Allowed(x.pchURL));
            }));
            callbacks.Add(Callback<HTML_JSAlert_t>.Create(x => { if (Owns(x.unBrowserHandle)) SteamHTMLSurface.JSDialogResponse(x.unBrowserHandle, false); }));
            callbacks.Add(Callback<HTML_JSConfirm_t>.Create(x => { if (Owns(x.unBrowserHandle)) SteamHTMLSurface.JSDialogResponse(x.unBrowserHandle, false); }));
            callbacks.Add(Callback<HTML_FileOpenDialog_t>.Create(x => { if (Owns(x.unBrowserHandle)) SteamHTMLSurface.FileLoadDialogResponse(x.unBrowserHandle, IntPtr.Zero); }));
            callbacks.Add(Callback<HTML_FinishedRequest_t>.Create(x => {
                if (x.unBrowserHandle == verifier && IsProfile(x.pchURL)
                    && new Uri(x.pchURL).Query == "?nr_identity=" + inspectionToken)
                    inspectAt = clock.ElapsedMilliseconds + 400;
            }));
            callbacks.Add(Callback<HTML_ChangedTitle_t>.Create(OnTitle));
            control = Create();
            verifier = Create();
            SteamHTMLSurface.LoadURL(control, Profile, null);
        }
        catch { Dispose(); throw; }
    }

    private bool Owns(HHTMLBrowser handle) => handle != HHTMLBrowser.Invalid && (handle == control || handle == verifier);
    private static bool Allowed(string value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme == "https" && uri.Host is "steamcommunity.com" or "store.steampowered.com" or "login.steampowered.com";
    private bool IsProfile(string value) => Uri.TryCreate(value, UriKind.Absolute, out var uri)
        && uri.Scheme == "https" && uri.Host == "steamcommunity.com" && uri.AbsolutePath == "/profiles/" + target + "/";

    private HHTMLBrowser Create()
    {
        HHTMLBrowser value = HHTMLBrowser.Invalid;
        bool done = false;
        using var ready = CallResult<HTML_BrowserReady_t>.Create((x, failed) => {
            if (!failed) value = x.unBrowserHandle;
            done = true;
        });
        ready.Set(SteamHTMLSurface.CreateBrowser("NightreignIdentity/1", null));
        Pump(() => done, 15000);
        if (value == HHTMLBrowser.Invalid) throw new TimeoutException("Steam 网页组件启动超时");
        SteamHTMLSurface.SetSize(value, 1024, 768);
        return value;
    }

    internal IdentityPage Inspect(int timeoutMs = 12000)
    {
        inspectionToken = Guid.NewGuid().ToString("N"); inspected = null; inspectAt = 0;
        SteamHTMLSurface.LoadURL(verifier, Profile + "?nr_identity=" + inspectionToken, null);
        Pump(() => inspected != null, timeoutMs);
        return inspected ?? throw new TimeoutException("无法核对 Steam 屏蔽状态，请保持 Steam 在线");
    }

    private string Guard => $$"""
        location.origin==='https://steamcommunity.com' && location.pathname==='/profiles/{{target}}/' &&
        String(window.g_steamID||'')==='{{account}}' && typeof window.g_sessionID==='string'
        """;

    internal void StartBlock(int watchdogMs = 9000)
    {
        EnsureAccount();
        blockToken = Guid.NewGuid().ToString("N"); actionTokens.Add(blockToken);
        // The browser's own watchdog also compensates after a late block response.
        // The native worker independently requests restoration at the same deadline.
        var script = $$"""
            (function(){
              const token='{{blockToken}}', account='{{account}}', target='{{target}}';
              const valid=()=>({{Guard}});
              const report=(t,tag,ok)=>{document.title='NRIDENT:'+JSON.stringify({token:t,tag:tag,ok:ok});};
              if(!valid()){report(token,'precondition',false);return;}
              const state={account,target,wanted:false,running:false,queued:false,tokens:[],blockFinished:false};
              window.__nrIdentity=state;
              const post=async b=>{
                if(!valid())return {tag:'precondition',ok:false};
                try{const r=await fetch('/actions/BlockUserAjax',{method:'POST',credentials:'same-origin',
                  headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},
                  body:new URLSearchParams({sessionID:g_sessionID,steamid:target,block:String(b)}).toString()});
                  return {tag:'response',ok:r.ok};
                }catch(e){return {tag:'unknown',ok:false};}
              };
              state.restore=async function(t){
                state.wanted=true; state.queued=true; if(t)state.tokens.push(t);
                if(state.running)return; state.running=true;
                let result;
                while(state.queued){state.queued=false;result=await post(0);}
                state.running=false;
                // A restore reply is a fence: no older restore or late-block
                // compensation remains queued in this browser when it is reported.
                if(state.blockFinished&&state.tokens.length){
                  const tokens=state.tokens.splice(0);
                  document.title='NRIDENT:'+JSON.stringify({tokens,tag:result.tag,ok:result.ok});}
              };
              state.timer=setTimeout(()=>state.restore(null),{{watchdogMs}});
              (async()=>{const result=await post(1);state.blockFinished=true;report(token,result.tag,result.ok);
                if(state.wanted)state.restore(null);})();
            })();
            """;
        SteamHTMLSurface.ExecuteJavascript(control, script);
    }

    internal IdentityReply? WaitBlock(int timeoutMs) => WaitReply(blockToken, timeoutMs);

    internal void RenewHold()
    {
        EnsureAccount();
        SteamHTMLSurface.ExecuteJavascript(control, $$"""
            (function(){if(!({{Guard}}))return; const s=window.__nrIdentity;
              if(s&&!s.wanted&&s.account==='{{account}}'&&s.target==='{{target}}'){
                clearTimeout(s.timer);s.timer=setTimeout(()=>s.restore(null),30000);} })();
            """);
    }

    internal IdentityReply? Restore(int timeoutMs = 7000)
    {
        EnsureAccount();
        var token = Guid.NewGuid().ToString("N"); actionTokens.Add(token);
        var script = $$"""
            (async function(){const token='{{token}}';
              const report=(tag,ok)=>{document.title='NRIDENT:'+JSON.stringify({token,tag,ok});};
              if(!({{Guard}})){report('precondition',false);return;}
              const state=window.__nrIdentity;
              if(state&&state.account==='{{account}}'&&state.target==='{{target}}'){
                clearTimeout(state.timer);state.restore(token);return;}
              try{const r=await fetch('/actions/BlockUserAjax',{method:'POST',credentials:'same-origin',
                headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},
                body:new URLSearchParams({sessionID:g_sessionID,steamid:'{{target}}',block:'0'}).toString()});
                report('response',r.ok);
              }catch(e){report('unknown',false);}
            })();
            """;
        SteamHTMLSurface.ExecuteJavascript(control, script);
        return WaitReply(token, timeoutMs);
    }

    private IdentityReply? WaitReply(string token, int timeoutMs)
    {
        Pump(() => replies.ContainsKey(token), timeoutMs);
        return replies.GetValueOrDefault(token);
    }
    internal void Wait(int milliseconds) => Pump(() => false, milliseconds);
    private void EnsureAccount()
    {
        if (!SteamUser.BLoggedOn() || SteamUser.GetSteamID().ToString() != account)
            throw new InvalidOperationException("需要原 Steam 账号在线以恢复通信");
    }
    private void Pump(Func<bool> done, int timeoutMs)
    {
        var until = clock.ElapsedMilliseconds + Math.Max(0, timeoutMs);
        while (!done() && clock.ElapsedMilliseconds < until)
        {
            SteamAPI.RunCallbacks(); tick();
            if (inspectAt != 0 && clock.ElapsedMilliseconds >= inspectAt)
            {
                inspectAt = 0;
                SteamHTMLSurface.ExecuteJavascript(verifier, $$"""
                    document.title='NRIDENT:'+JSON.stringify({token:'{{inspectionToken}}',page:true,
                      account:({{Guard}})&&location.search==='?nr_identity={{inspectionToken}}'&&!!document.querySelector('#account_pulldown'),
                      block:!!document.querySelector('[onclick*="ConfirmBlock"],a[href*="ConfirmBlock"]'),
                      unblock:!!document.querySelector('[onclick*="ConfirmUnblock"],a[href*="ConfirmUnblock"]'),
                      friend:!!document.querySelector('[onclick*="RemoveFriend"],a[href*="RemoveFriend"]')});
                    """);
            }
            Thread.Sleep(25);
        }
    }
    private void OnTitle(HTML_ChangedTitle_t x)
    {
        if (!Owns(x.unBrowserHandle) || !x.pchTitle.StartsWith("NRIDENT:", StringComparison.Ordinal)) return;
        try
        {
            using var document = JsonDocument.Parse(x.pchTitle[8..]); var root = document.RootElement;
            var token = root.TryGetProperty("token", out var tokenProperty) ? tokenProperty.GetString() ?? "" : "";
            bool Flag(string name) => root.TryGetProperty(name, out var p) && p.ValueKind == JsonValueKind.True;
            if (x.unBrowserHandle == verifier && token == inspectionToken && Flag("page"))
                inspected = new(Flag("account"), Flag("block"), Flag("unblock"), Flag("friend"));
            else if (x.unBrowserHandle == control)
            {
                var tag = root.GetProperty("tag").GetString() ?? "unknown";
                var tokens = root.TryGetProperty("tokens", out var list) && list.ValueKind == JsonValueKind.Array
                    ? list.EnumerateArray().Select(item => item.GetString() ?? "").ToArray() : new[] { token };
                foreach (var item in tokens)
                {
                    if (!actionTokens.Remove(item)) continue;
                    replies[item] = new(tag, Flag("ok"));
                    if (item == blockToken && tag is "response" or "precondition") BlockSettled = true;
                }
            }
        }
        catch (JsonException) { }
        catch (InvalidOperationException) { }
        catch (KeyNotFoundException) { }
    }
    public void Dispose()
    {
        if (!initialized) return;
        if (control != HHTMLBrowser.Invalid) SteamHTMLSurface.RemoveBrowser(control);
        if (verifier != HHTMLBrowser.Invalid) SteamHTMLSurface.RemoveBrowser(verifier);
        foreach (var callback in callbacks) callback.Dispose();
        callbacks.Clear(); SteamHTMLSurface.Shutdown(); initialized = false;
    }
}
