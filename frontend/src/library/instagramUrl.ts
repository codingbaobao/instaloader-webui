export function canonicalInstagramUrl(
  value: string | null | undefined,
): string | null {
  if (!value) return null;
  try {
    const parsed = new URL(value);
    const queryEntries = [...parsed.searchParams.entries()];
    const validQuery = queryEntries.length === 0 || (
      queryEntries.length === 1
      && queryEntries[0][0] === "img_index"
      && /^[1-9][0-9]{0,3}$/.test(queryEntries[0][1])
    );
    if (
      parsed.protocol !== "https:"
      || parsed.hostname !== "www.instagram.com"
      || parsed.username
      || parsed.password
      || parsed.port
      || parsed.hash
      || !validQuery
    ) {
      return null;
    }
    return parsed.toString();
  } catch {
    return null;
  }
}
