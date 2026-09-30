using NightreignP2PHelper;

internal static class CommendationTests
{
    internal static void Run(Action<string, Action> test)
    {
        void Assert(bool ok) { if (!ok) throw new Exception("commendation reservation invalid"); }
        test("comment reservation survives crash before response and expires at seven days", () => {
            var now = DateTimeOffset.UtcNow;
            var record = CommendationHistory.Reserve("76561198049468922", "76561198049468923", now);
            Assert(record.State == "sending" && CommendationHistory.CoolingDown(record, now.AddDays(6)));
            Assert(!CommendationHistory.CoolingDown(record, now.AddDays(7)));
            var path = Path.Combine(Path.GetTempPath(), Guid.NewGuid()+".json");
            try { IdentityFiles.Write(path, record); Assert(IdentityFiles.Read<CommendationRecord>(path) == record); }
            finally { File.Delete(path); }
        });
        test("success and ambiguous results retain duplicate protection", () => {
            var now = DateTimeOffset.UtcNow;
            var record = CommendationHistory.Reserve("a", "b", now);
            foreach (var result in new[] { "sent", "unknown" })
                Assert(CommendationHistory.Complete(record, result, now).CooldownUntil == now.AddDays(7));
        });
        test("explicit rejection permits later manual retry after short backoff", () => {
            var now = DateTimeOffset.UtcNow;
            var record = CommendationHistory.Reserve("a", "b", now);
            var denied = CommendationHistory.Complete(record, "rejected", now);
            Assert(CommendationHistory.CoolingDown(denied, now.AddSeconds(59)));
            Assert(!CommendationHistory.CoolingDown(denied, now.AddSeconds(60)));
        });
    }
}
