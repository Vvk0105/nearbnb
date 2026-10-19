import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { SearchResult } from "../lib/types";
import { PropertyCard } from "../components/PropertyCard";
import { EMPTY_SEARCH, SearchBar, searchToParams } from "../components/SearchBar";
import { Icon, type IconName } from "../components/icons";
import { useAuth } from "../lib/auth";

const STEPS: { icon: IconName; title: string; text: string }[] = [
  { icon: "search", title: "Search by the hour", text: "Pick the day and the time you want to arrive, and how many nights. We only show places that are really free." },
  { icon: "booking", title: "Send a request", text: "The host replies within a set time. Your dates are held for you while they decide, and you are not charged yet." },
  { icon: "card", title: "Pay and stay", text: "Once the host accepts, pay securely by card. Your booking is confirmed and the host's details are shared." },
];

const PERKS: { icon: IconName; title: string; text: string }[] = [
  { icon: "calendar", title: "Check in at any hour", text: "Not just 2 pm. Choose the hour that suits your trip." },
  { icon: "shield", title: "Secure payments", text: "Card payments run through Stripe. Hosts are paid after your stay begins." },
  { icon: "star", title: "Honest reviews", text: "Reviews are blind: both sides write theirs before either is shown." },
];

export default function Home() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { data } = useQuery({ queryKey: ["recent"], queryFn: () => api<SearchResult>("/search?sort=newest&page_size=6") });

  return (
    <div className="space-y-16">
      <section className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 via-brand-900 to-brand-700 px-6 py-16 text-center text-white md:px-12 md:py-20">
        <div aria-hidden="true" className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-brand-400/20 blur-3xl" />
        <div aria-hidden="true" className="pointer-events-none absolute -bottom-32 -left-16 h-72 w-72 rounded-full bg-brand-300/10 blur-3xl" />
        <p className="relative mx-auto mb-4 inline-block rounded-full bg-white/10 px-4 py-1 text-xs font-medium uppercase tracking-wider text-brand-100">Book by the hour, stay by the day</p>
        <h1 className="relative mx-auto max-w-3xl text-4xl font-extrabold tracking-tight md:text-6xl">Find a place for exactly the time you need</h1>
        <p className="relative mx-auto mt-4 max-w-2xl text-lg text-brand-50/90">Check in at any hour. Stay for 24, 48, 72 hours or more. See real availability before you ask.</p>
        <div className="relative mx-auto mt-10 max-w-5xl text-left text-ink">
          <SearchBar initial={EMPTY_SEARCH} onSearch={(v) => navigate(`/search?${searchToParams(v)}`)} />
        </div>
      </section>

      <section aria-label="How it works">
        <h2 className="text-center text-2xl font-bold tracking-tight">How it works</h2>
        <ol className="mt-8 grid gap-6 md:grid-cols-3">
          {STEPS.map((s, i) => (
            <li key={s.title} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
              <div className="flex items-center gap-3">
                <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-brand-50 text-brand-700"><Icon name={s.icon} /></span>
                <span className="text-sm font-semibold text-slate-400">Step {i + 1}</span>
              </div>
              <h3 className="mt-4 text-lg font-semibold">{s.title}</h3>
              <p className="mt-1 text-sm text-slate-600">{s.text}</p>
            </li>
          ))}
        </ol>
      </section>

      {data && data.items.length > 0 && (
        <section>
          <div className="mb-5 flex items-end justify-between">
            <h2 className="text-2xl font-bold tracking-tight">Recently added</h2>
            <Link to="/search" className="text-sm font-medium text-brand-700 hover:underline">See all places</Link>
          </div>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            {data.items.map((item) => <PropertyCard key={item.id} item={item} carry="" />)}
          </div>
        </section>
      )}

      <section aria-label="Why NearBnB" className="grid gap-6 md:grid-cols-3">
        {PERKS.map((p) => (
          <div key={p.title} className="flex gap-4">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-600 text-white"><Icon name={p.icon} className="h-5 w-5" /></span>
            <div>
              <h3 className="font-semibold">{p.title}</h3>
              <p className="mt-0.5 text-sm text-slate-600">{p.text}</p>
            </div>
          </div>
        ))}
      </section>

      {!user && (
        <section className="rounded-3xl bg-slate-900 px-8 py-12 text-center text-white">
          <h2 className="text-2xl font-bold tracking-tight">Have a place to share?</h2>
          <p className="mx-auto mt-2 max-w-xl text-slate-300">Become a host, set your own price, and decide who stays.</p>
          <Link to="/register" className="mt-6 inline-block rounded-lg bg-brand-500 px-6 py-2.5 text-sm font-semibold text-white hover:bg-brand-400">Sign up to host</Link>
        </section>
      )}
    </div>
  );
}
