namespace NightreignP2PHelper;

internal enum IdentitySessionResult { Idle, Rejected, Unavailable, Completed }

internal readonly record struct IdentityRequestEnvironment(
    DateTimeOffset Now, bool ClientAlive, bool Cancelled, bool GameRunning);

// This is the production connection boundary. Recovery's entire blocking loop
// is one work call, so no idle cleanup can destroy its in-flight HTML context.
internal static class IdentitySessionCoordinator
{
    internal static void Release(ref bool initialized, Action shutdown)
    {
        // Steamworks.NET's Shutdown decrements its callback dispatcher count.
        // Calling it after a failed Init poisons the next successful session.
        if (!initialized) return;
        try { shutdown(); }
        finally { initialized = false; }
    }

    internal static IdentitySessionResult Run(IdentityRequest? request, bool recoveryRequired,
        Func<IdentityRequest, IdentityRequestEnvironment> inspect,
        Func<bool> connect, Action work, Action disconnect)
    {
        if (!recoveryRequired)
        {
            if (request is null) return IdentitySessionResult.Idle;
            if (!CanStart(request, inspect(request))) return IdentitySessionResult.Rejected;
        }
        try
        {
            if (!connect()) return IdentitySessionResult.Unavailable;
            // Login/initialization can take long enough for a queued request to
            // expire, be cancelled, or lose its game. Recheck before any action.
            if (!recoveryRequired && !CanStart(request!, inspect(request!))) return IdentitySessionResult.Rejected;
            work();
            return IdentitySessionResult.Completed;
        }
        finally { disconnect(); }
    }

    private static bool CanStart(IdentityRequest request, IdentityRequestEnvironment context)
        => Guid.TryParseExact(request.Transaction, "N", out _)
        && Guid.TryParseExact(request.Client, "N", out _)
        && IdentityRules.ValidSteamId(request.Target)
        && context.Now - request.CreatedAt <= TimeSpan.FromSeconds(15)
        && request.CreatedAt <= context.Now.AddSeconds(5)
        && context.ClientAlive && !context.Cancelled && context.GameRunning;
}
