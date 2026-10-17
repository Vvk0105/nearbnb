export type SocialLink = { id: string; platform: string; url: string };
export type PendingConsent = { doc_type: string; version: number; title: string };

export type User = {
  id: string;
  email: string;
  full_name: string;
  phone: string | null;
  dob: string | null;
  photo_url: string | null;
  status: string;
  email_verified: boolean;
  is_admin: boolean;
  totp_enabled: boolean;
  pending_email: string | null;
  last_login_at: string | null;
  social_links: SocialLink[];
  pending_consents: PendingConsent[];
  created_at: string | null;
};

export type SettingItem = {
  key: string;
  group: string;
  label: string;
  description: string;
  type: "int" | "float" | "bool" | "str" | "choice" | "str_list" | "json";
  unit: string;
  min: number | null;
  max: number | null;
  choices: (string | number)[];
  default: unknown;
  value: unknown;
  source: string;
  version: number | null;
};

export type AuditEntry = {
  id: number;
  action: string;
  entity_type: string;
  entity_id: string | null;
  actor: string | null;
  before: unknown;
  after: unknown;
  reason: string | null;
  ip: string | null;
  created_at: string;
};

export type SessionInfo = { id: string; user_agent: string | null; ip: string | null; created_at: string; current: boolean };

export type HostStep = { key: string; label: string; done: boolean };
export type HostDoc = { id: string; doc_type: string; original_name: string; size_bytes: number; content_type: string; created_at: string | null };
export type StripeInfo = {
  connected: boolean; details_submitted: boolean; payouts_enabled: boolean; transfers_active: boolean;
  ready: boolean; requirements: string[]; dev_mode: boolean; account_id?: string;
};
export type HostInfo = {
  state: string;
  id?: string;
  review_status?: string;
  review_note?: string | null;
  suspended_reason?: string | null;
  application?: Record<string, string>;
  editable?: boolean;
  documents?: HostDoc[];
  steps?: HostStep[];
  stripe?: StripeInfo;
  can_create_listings?: boolean;
  can_submit_listings?: boolean;
  submitted_at?: string | null;
};

export type Photo = { id: string; url: string; thumb_url: string; position: number; status: string };
export type Room = { id: string; label: string };
export type UnitData = {
  id: string; property_id: string; name: string; description: string; price_per_24h: number; currency: string;
  cleaning_fee: number; capacity: number; quantity: number; bedrooms: number | null; bathrooms: number | null;
  beds: number | null; max_stay_hours: number | null; checkin_from: string; checkin_to: string; status: string;
  review_note: string | null; amenities: { id: string; name: string }[]; rooms: Room[]; photos: Photo[];
};
export type PendingRevision = { id: string; entity_type: string; entity_id: string; fields: string[]; photos_changed: boolean };
export type PropertyData = {
  id: string; name: string; description: string; rules: string; type_id: string | null; type_name: string | null;
  address_line1: string; address_line2: string; suburb: string; state: string; postcode: string; country: string;
  latitude: number | null; longitude: number | null; timezone: string; status: string; photos: Photo[]; units: UnitData[];
  checkin_instructions?: string; cancellation_template_key?: string | null; review_note?: string | null;
  pending_revisions?: PendingRevision[];
  checklist?: { scope: string; item: string; message: string }[];
  result?: { applied: string[]; pending_review: string[] };
};
export type ListingMeta = { property_types: { id: string; name: string }[]; amenities: { id: string; name: string }[] };
export type CalendarData = {
  timezone: string; rooms: Room[];
  entries: { id: string; room_id: string; room_label: string; kind: string; note: string | null; start_local: string; end_local: string }[];
};

// ---- guest side (Phase 3) ----
export type SearchItem = {
  id: string; name: string; type_name: string | null; suburb: string; state: string;
  cover_url: string | null; thumb_url: string | null; currency: string; min_price: number; distance_km: number | null;
  units: { id: string; name: string; price_per_24h: number; capacity: number }[];
};
export type SearchResult = { total: number; page: number; page_size: number; items: SearchItem[]; hint: string | null };

export type BookingMeta = { interval_minutes: number; step_hours: number; max_stay_hours: number; min_lead_hours: number; max_advance_days: number };
export type UnitRules = { interval_minutes: number; step_hours: number; max_stay_hours: number; min_lead_hours: number; max_advance_days: number };
export type PublicUnit = UnitData & { rules: UnitRules };
export type PublicProperty = Omit<PropertyData, "units" | "checklist" | "pending_revisions" | "result"> & { units: PublicUnit[]; host_first_name: string };

export type DayStatus = "available" | "partial" | "unavailable" | "past" | "closed";
export type CalendarMonth = { timezone: string; year: number; month: number; days: { date: string; status: DayStatus }[] };
export type Timeline = { timezone: string; date: string; rooms: number; occupied: { start_local: string; end_local: string; kind: "booked" | "unavailable"; label: string }[] };
export type PriceBreakdown = {
  nights: number; hours: number; accommodation: number; cleaning_fee: number; service_fee: number; service_fee_percent: number;
  tax: number; tax_name: string | null; tax_rate_percent: number; tax_inclusive: boolean; total: number;
};
export type Alternative = { checkin_local: string; checkout_local: string; label: string };
export type Quote = {
  available: boolean; code?: string; message?: string; alternatives: Alternative[]; timezone: string; hours: number; guests: number;
  checkin_local?: string; checkout_local?: string; checkin_label?: string; checkout_label?: string; currency?: string;
  rooms_free?: number; price?: PriceBreakdown; response_minutes?: number;
};
export type Place = {
  label: string; latitude: number; longitude: number;
  address: { address_line1: string; suburb: string; state: string; postcode: string; country: string };
};

// ---- booking requests (Phase 4) ----
export type BookingStatus =
  | "requested" | "accepted" | "payment_pending" | "confirmed" | "checked_in" | "completed"
  | "rejected" | "expired" | "withdrawn" | "payment_expired" | "cancelled";
export type BookingGroup = "pending" | "awaiting_payment" | "upcoming" | "past";
export type BookingItem = {
  id: string; reference: string; status: BookingStatus; hours: number; guests: number; currency: string; timezone: string;
  check_in_local: string; check_out_local: string; check_in_label: string; check_out_label: string;
  requested_at: string | null; expires_at: string | null; payment_deadline_at: string | null;
  seconds_to_respond: number | null; seconds_to_pay: number | null; closed_reason: string | null; can_withdraw: boolean;
  can_pay?: boolean; invoice_number?: string | null; confirmed_at?: string | null;
  payment?: { status: string; failure_message: string | null; intent_id?: string; charge_id?: string | null; failure_code?: string | null };
  property: { id: string; name: string; suburb: string; state: string; cover_url: string | null };
  unit: { id: string; name: string }; room: string | null;
  // guest view
  price: Partial<PriceBreakdown> & { total?: number; commission?: number; commission_percent?: number; host_payable?: number };
  host?: { first_name?: string; name?: string; email?: string; phone?: string | null };
  checkin_instructions?: string;
  // host / admin view
  guest?: {
    full_name: string; age: number | null; member_since: string | null; email_verified: boolean;
    social_links: { platform: string; url: string }[]; email?: string; phone?: string; dob?: string;
  };
};
export type BookingList = { items: BookingItem[]; total: number; page: number; page_size: number; counts: Record<BookingGroup, number> };
export type Readiness = { email_verified: boolean; ready: boolean; missing: { field: string; label: string }[] };

// ---- payment (Phase 5) ----
export type PayStart = { booking_id: string; client_secret: string | null; amount: number; currency: string; publishable_key: string | null; fake: boolean; seconds_to_pay: number | null };
export type InvoiceData = {
  number: string; issued_at: string; total: number; currency: string; footer: string;
  seller: { name: string; tax_id: string; address: string };
  buyer: { name: string; email: string };
  stay: { reference: string; property: string; unit: string; address: string; check_in: string; check_out: string; timezone: string; hours: number; nights: number; guests: number };
  lines: { description: string; amount: number }[];
  tax: { name: string | null; rate_percent: number | null; amount: number; inclusive: boolean };
  payment: { reference: string; paid_at: string };
};
