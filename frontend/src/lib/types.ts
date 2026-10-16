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
