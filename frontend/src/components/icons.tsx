import type { ReactNode } from "react";

// A small inline icon set (24x24, stroke based) so the app needs no icon library.
const PATHS: Record<string, ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
  users: <><circle cx="9" cy="8" r="3.5" /><path d="M2.5 20c0-3.5 2.9-6 6.5-6s6.5 2.5 6.5 6" /><path d="M16 4.5a3.5 3.5 0 0 1 0 7M18 14.5c2.3.6 3.5 2.6 3.5 5.5" /></>,
  host: <><path d="M3 11l9-7 9 7" /><path d="M5 10v10h14V10" /><path d="M10 20v-6h4v6" /></>,
  home: <><path d="M3 11l9-7 9 7" /><path d="M5 10v10h14V10" /></>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M3 10h18M8 3v4M16 3v4" /></>,
  booking: <><path d="M5 3h14v18l-3-2-2 2-2-2-2 2-2-2-3 2z" /><path d="M9 8h6M9 12h6" /></>,
  card: <><rect x="2.5" y="5" width="19" height="14" rx="2" /><path d="M2.5 10h19M6 15h4" /></>,
  wallet: <><path d="M3 7a2 2 0 0 1 2-2h13v4" /><path d="M3 7v11a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2z" /><circle cx="16.5" cy="14.5" r="1" /></>,
  refund: <><path d="M4 12a8 8 0 1 0 3-6.2" /><path d="M4 4v4h4" /></>,
  alert: <><path d="M12 3l10 18H2z" /><path d="M12 10v5M12 18v.5" /></>,
  flag: <><path d="M5 21V4" /><path d="M5 4h12l-2 4 2 4H5" /></>,
  star: <path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z" />,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" /></>,
  doc: <><path d="M6 3h8l5 5v13H6z" /><path d="M14 3v5h5M9 13h7M9 17h7" /></>,
  shield: <><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /><path d="M9 12l2 2 4-4" /></>,
  list: <><path d="M8 6h13M8 12h13M8 18h13" /><circle cx="4" cy="6" r="1" /><circle cx="4" cy="12" r="1" /><circle cx="4" cy="18" r="1" /></>,
  download: <><path d="M12 3v12M7 10l5 5 5-5" /><path d="M4 20h16" /></>,
  bolt: <path d="M13 2L4 14h7l-1 8 9-12h-7z" />,
  tag: <><path d="M3 12V4h8l10 10-8 8z" /><circle cx="7.5" cy="8.5" r="1" /></>,
  percent: <><path d="M19 5L5 19" /><circle cx="7" cy="7" r="2.5" /><circle cx="17" cy="17" r="2.5" /></>,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  logout: <><path d="M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5" /><path d="M15 8l4 4-4 4M19 12H9" /></>,
  external: <><path d="M14 4h6v6" /><path d="M20 4L10 14" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></>,
  check: <path d="M5 12l5 5L20 7" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  search: <><circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  bell: <><path d="M6 17V11a6 6 0 1 1 12 0v6l2 2H4z" /><path d="M10 21h4" /></>,
  trip: <><path d="M3 12l18-8-8 18-2-8z" /></>,
  money: <><circle cx="12" cy="12" r="9" /><path d="M14.5 9a2.5 2 0 0 0-2.5-1.5c-1.5 0-2.5.8-2.5 2s1 1.7 2.5 2 2.5.8 2.5 2-1 2-2.5 2A2.5 2 0 0 1 9.5 15M12 6v1.5M12 16.5V18" /></>,
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, className = "h-5 w-5" }: { name: IconName; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}
