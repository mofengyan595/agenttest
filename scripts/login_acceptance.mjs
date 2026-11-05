// A conservative rolling-window estimate also covers a target's fixed windows.
// All positions share one source. Account keys are operator-assigned aliases, never usernames.
export class LoginSchedule {
  constructor({source, account}) {
    for (const {maxRequests, windowSeconds} of [source, account]) {
      if (!Number.isSafeInteger(maxRequests) || maxRequests < 1 ||
          !Number.isSafeInteger(windowSeconds) || windowSeconds < 1) {
        throw new Error("Verified source and account login limits are required");
      }
    }
    this.source = source;
    this.account = account;
    this.attempts = [];
    this.blockedUntil = 0;
  }

  earliest(now, expected) {
    const needed = Object.values(expected);
    if (needed.some(count => !Number.isSafeInteger(count) || count < 0)) {
      throw new Error("Login estimates must be nonnegative integers");
    }
    const total = needed.reduce((sum, count) => sum + count, 0);
    if (total === 0) return now;
    const availableAt = (limit, count, attempts) => {
      if (count > limit.maxRequests) throw new Error("Position exceeds a login window by itself");
      const window = limit.windowSeconds * 1000;
      const active = attempts.filter(attempt => attempt.at + window > now);
      const release = active[active.length + count - limit.maxRequests - 1];
      return release ? release.at + window : now;
    };
    return Math.max(now, this.blockedUntil, availableAt(this.source, total, this.attempts),
      ...Object.entries(expected).filter(([, count]) => count > 0).map(([alias, count]) =>
        availableAt(this.account, count, this.attempts.filter(attempt => attempt.alias === alias))));
  }

  observe(at, alias, status, retryAfterSeconds) {
    this.attempts.push({at, alias});
    if (status === 429) {
      const seconds = Number.isSafeInteger(retryAfterSeconds) && retryAfterSeconds >= 0
        ? retryAfterSeconds : Math.max(this.source.windowSeconds, this.account.windowSeconds);
      // A 429 cannot identify its bucket reliably. Hold all later login work on this source.
      this.blockedUntil = Math.max(this.blockedUntil, at + seconds * 1000);
    }
  }
}
