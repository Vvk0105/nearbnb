export const money = (cents: number, currency = "AUD") =>
  new Intl.NumberFormat("en-AU", { style: "currency", currency }).format(cents / 100);

export const toCents = (dollars: string): number => Math.round(Number.parseFloat(dollars || "0") * 100);
export const fromCents = (cents: number): string => (cents / 100).toFixed(2);

export const label = (value: string): string => value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export const FIELD_LABELS: Record<string, string> = {
  address_line1: "Street address", address_line2: "Address line 2", suburb: "Suburb", state: "State", postcode: "Postcode",
  country: "Country", latitude: "Latitude", longitude: "Longitude", timezone: "Timezone", type_id: "Property type",
  description: "Description", name: "Name", rules: "House rules", cancellation_template_key: "Cancellation policy",
  price_per_24h: "Price per 24 hours", cleaning_fee: "Cleaning fee", capacity: "Guest capacity", quantity: "Number of rooms",
  bedrooms: "Bedrooms", bathrooms: "Bathrooms", beds: "Beds", amenity_ids: "Amenities",
};
export const fieldLabel = (f: string) => FIELD_LABELS[f] ?? label(f);

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
export const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

/** "2026-11-03" -> "Tue 3 Nov". Fixed wording (not the browser's locale) so it matches the server's messages everywhere. */
export function dayLabel(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return `${WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()]} ${d} ${MONTHS_SHORT[m - 1]}`;
}

/** "14:00" -> "2:00 PM" */
export function timeLabel(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
}

/** "just now", "5 min ago", "3 h ago", "2 days ago", then the date. */
export function timeAgo(isoTime: string, now: number = Date.now()): string {
  const minutes = Math.floor((now - new Date(isoTime).getTime()) / 60000);
  if (Number.isNaN(minutes) || minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.floor(minutes / 60)} h ago`;
  if (minutes < 43200) { const d = Math.floor(minutes / 1440); return `${d} day${d > 1 ? "s" : ""} ago`; }
  const d = new Date(isoTime);
  return `${d.getDate()} ${MONTHS_SHORT[d.getMonth()]} ${d.getFullYear()}`;
}

export const todayIso = (): string => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

/** Check-in times offered by the picker: every `intervalMinutes` between `from` and `to` ("HH:MM"). */
export function timeOptions(intervalMinutes: number, from = "00:00", to = "23:59"): string[] {
  const toMin = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3, 5));
  const out: string[] = [];
  for (let m = Math.ceil(toMin(from) / intervalMinutes) * intervalMinutes; m <= toMin(to); m += intervalMinutes) {
    out.push(`${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`);
  }
  return out;
}
