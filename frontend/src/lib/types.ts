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
