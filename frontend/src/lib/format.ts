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
