export function formatDate(value: string | null): string {
  if (value === null) {
    return "Not yet";
  }
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return "Unknown date";
  }
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
}

export function formatDateTime(value: string | null): string {
  if (value === null) {
    return "Not yet";
  }
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) {
    return "Unknown date";
  }
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(date);
}
