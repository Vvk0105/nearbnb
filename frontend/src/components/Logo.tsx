import { Link } from "react-router-dom";

/** Text logo. Replace the contents (or swap in an <img>) when the client provides a brand. */
export function Logo({ className = "" }: { className?: string }) {
  return (
    <Link to="/" aria-label="NearBnB home" className={`text-2xl font-extrabold tracking-tight text-ink ${className}`}>
      Near<span className="text-brand-600">BnB</span>
    </Link>
  );
}
