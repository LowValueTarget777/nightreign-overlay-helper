namespace NightreignP2PHelper;

internal sealed record CommendationRecord(string Account, string Target, string State,
    DateTimeOffset AttemptedAt, DateTimeOffset CooldownUntil);

internal static class CommendationHistory
{
    internal static bool CoolingDown(CommendationRecord? previous, DateTimeOffset now)
        => previous is not null && previous.CooldownUntil > now;

    // Record before posting. A crash or ambiguous response keeps the week's
    // reservation so restarting/clicking again cannot post a duplicate.
    internal static CommendationRecord Reserve(string account, string target, DateTimeOffset now)
        => new(account, target, "sending", now, now.AddDays(7));

    internal static CommendationRecord Complete(CommendationRecord record, string result, DateTimeOffset now)
        => record with { State = result, CooldownUntil = result == "rejected" || result == "precondition"
            ? now.AddSeconds(60) : record.CooldownUntil };
}
