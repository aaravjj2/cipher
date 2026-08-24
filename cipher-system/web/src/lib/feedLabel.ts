/** Display label for a provider feed token. Yahoo/yfinance is delayed, never live. */
export function isYahooFeed(feed: string | null | undefined): boolean {
  const value = (feed ?? "").toLowerCase();
  return value === "yahoo" || value === "yfinance";
}

export function feedLabel(feed: string | null | undefined): string {
  if (!feed) return "unknown";
  return isYahooFeed(feed) ? "yahoo delayed" : feed;
}
