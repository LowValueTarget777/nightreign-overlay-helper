using Steamworks;
using System.Text.Json;

namespace NightreignP2PHelper;

internal static class CommendationWorker
{
    private sealed record Request(string Target, string Comment, string ExpectedAccount);
    private static void Reply(string state, string message, string account = "", DateTimeOffset? until = null)
    {
        Console.WriteLine(JsonSerializer.Serialize(new { state, message, account, cooldown_until = until }, IdentityFiles.Json));
        Console.Out.Flush();
    }

    internal static int Run(bool checkOnly = false, bool inspectOnly = false)
    {
        Console.InputEncoding = System.Text.Encoding.UTF8;
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        CommendationRecord? reservation = null;
        string? path = null;
        try
        {
            var line = Console.ReadLine();
            if (line is null || line.Length > 8192) { Reply("error", "留言请求无效"); return 1; }
            var request = JsonSerializer.Deserialize<Request>(line, IdentityFiles.Json);
            if (request is null || !IdentityRules.ValidSteamId(request.Target) || string.IsNullOrWhiteSpace(request.Comment)
                || request.Comment.Length > 500 || request.Comment.Contains('\0'))
            { Reply("error", "请填写 1–500 字的留言"); return 1; }
            if (!IdentityWorker.Connect()) { Reply("error", "请保持 Steam 原账号在线后重试"); return 1; }
            var account = SteamUser.GetSteamID().ToString();
            if (account == request.Target || (!string.IsNullOrEmpty(request.ExpectedAccount) && request.ExpectedAccount != account))
            { Reply("error", "Steam 账号已切换或目标为自己，请重新确认队友", account); return 1; }
            if (inspectOnly)
            {
                using var check = new IdentityHtmlSession(account, request.Target, () => { });
                try
                {
                    var inspectedPage = check.Inspect();
                    Console.WriteLine(JsonSerializer.Serialize(new { state = "inspected", correct_account = inspectedPage.CorrectAccount,
                        can_block = inspectedPage.CanBlock, can_unblock = inspectedPage.CanUnblock, comments_ready = check.CommentsReady,
                        probes = check.InspectionProbes, replies = check.InspectionReplies }, IdentityFiles.Json));
                }
                catch (TimeoutException)
                {
                    Console.WriteLine(JsonSerializer.Serialize(new { state = "page_timeout",
                        probes = check.InspectionProbes, replies = check.InspectionReplies, page = check.InspectionState }, IdentityFiles.Json));
                }
                return 0;
            }
            if (checkOnly)
            {
                using var check = new IdentityHtmlSession(account, request.Target, () => { });
                var present = check.CommentPresent(request.Comment);
                Reply(present ? "verified" : "not_found", present ? "新加载主页确认找到本账号的测试留言" : "未找到相同留言", account);
                return 0;
            }
            var root = Path.Combine(IdentityFiles.Root, "commendations");
            Directory.CreateDirectory(root);
            using var targetLock = new FileStream(Path.Combine(root, account + "-" + request.Target + ".lock"),
                FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None);
            path = Path.Combine(root, account + "-" + request.Target + ".json");
            var previous = IdentityFiles.Read<CommendationRecord>(path);
            if (previous is not null && (previous.Account != account || previous.Target != request.Target))
                throw new InvalidDataException();
            if (CommendationHistory.CoolingDown(previous, DateTimeOffset.UtcNow))
            { Reply("cooldown", "该队友仍在留言间隔内", account, previous!.CooldownUntil); return 0; }
            using var page = new IdentityHtmlSession(account, request.Target, () => { });
            var inspected = page.Inspect();
            if (!inspected.CorrectAccount || !page.CommentsReady)
            { Reply("error", "主页未开放留言或网页未登录原账号，可以点击资料页手动查看", account); return 0; }
            // Posting is inappropriate until our temporary communication block
            // has been restored. Leave the reservation untouched in this case.
            if (inspected.CanUnblock)
            { Reply("error", "队友通信尚未恢复，请等待解除后再点赞", account); return 0; }
            reservation = CommendationHistory.Reserve(account, request.Target, DateTimeOffset.UtcNow);
            IdentityFiles.Write(path, reservation);
            var response = page.PostComment(request.Comment);
            var outcome = response?.Tag switch {
                "comment_sent" => "sent", "comment_rejected" => "rejected",
                "comment_precondition" => "precondition", _ => "unknown" };
            reservation = CommendationHistory.Complete(reservation, outcome, DateTimeOffset.UtcNow);
            IdentityFiles.Write(path, reservation);
            Reply(outcome, outcome switch {
                "sent" => "已留言点赞，七天内不重复发送",
                "rejected" => "Steam 未接受留言，请稍后重试或查看主页",
                "precondition" => "网页状态改变，未发送留言，请重新查看主页",
                _ => "发送结果不确定，请到主页核对；七天内暂停重复发送"
            }, account, reservation.CooldownUntil);
            return 0;
        }
        catch
        {
            Reply(reservation is null ? "error" : "unknown", reservation is null
                ? "留言准备失败，请保持 Steam 在线；没有自动重发"
                : "发送结果不确定，已保留七天间隔，请到主页核对", reservation?.Account ?? "", reservation?.CooldownUntil);
            return 1;
        }
        finally { IdentityWorker.Disconnect(); }
    }
}
