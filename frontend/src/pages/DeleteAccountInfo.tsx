import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";

/**
 * Public page explaining how to delete a NearBnB account and what happens to the data.
 * Google Play asks every app with sign-up for a web link like this one, reachable without the app and without signing in.
 */
export default function DeleteAccountInfoPage() {
  const { user } = useAuth();
  return (
    <article className="mx-auto max-w-2xl space-y-6 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm md:p-8">
      <h1 className="text-2xl font-bold tracking-tight">Delete your NearBnB account</h1>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">How to delete it</h2>
        <ol className="list-decimal space-y-1 pl-5 text-slate-700">
          <li>
            <strong>In the app:</strong> open Profile, then Delete account, and confirm with your password.
          </li>
          <li>
            <strong>On the website:</strong> sign in, go to Account, then Security, then Delete account, and confirm with your password.
          </li>
        </ol>
        {user ? (
          <Link to="/account/security" className="inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
            Go to Security settings
          </Link>
        ) : (
          <Link to="/login" className="inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700">
            Sign in to delete your account
          </Link>
        )}
        <p className="text-sm text-slate-600">Cannot sign in? Contact our support team from the email address on your account and we will delete it for you.</p>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">What is deleted</h2>
        <p className="text-slate-700">
          Straight away: your name, email address, phone number, date of birth, profile photo, social links, notifications, saved phones for
          notifications, and every signed-in session.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">What is kept</h2>
        <p className="text-slate-700">
          Records of past bookings, payments, refunds and reviews, without your contact details. We keep them because tax and consumer law
          require it and because the host needs their own history. Card details are never stored by NearBnB; they stay with our payment
          provider, Stripe.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Before you delete</h2>
        <p className="text-slate-700">
          Bookings that are still in progress must be finished or cancelled first. Host accounts are closed by our support team, so we can
          settle payouts properly.
        </p>
      </section>
    </article>
  );
}
