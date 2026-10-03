export function relativeTime(seconds) {
  if (!seconds) return "never";
  const delta = Math.max(0, Date.now() / 1000 - seconds);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)} min ago`;
  if (delta < 86_400) return `${Math.floor(delta / 3600)} h ago`;
  return `${Math.floor(delta / 86_400)} d ago`;
}

export function deviceName(devices, id) {
  return devices.find((d) => d.id === id)?.name ?? id;
}

export function firedCount(count) {
  if (!count) return "never fired";
  return count === 1 ? "fired once" : `fired ${count} times`;
}
