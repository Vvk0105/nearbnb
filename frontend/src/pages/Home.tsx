import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import type { SearchResult } from "../lib/types";
import { PropertyCard } from "../components/PropertyCard";
import { EMPTY_SEARCH, SearchBar, searchToParams } from "../components/SearchBar";
import { useAuth } from "../lib/auth";

export default function Home() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { data } = useQuery({ queryKey: ["recent"], queryFn: () => api<SearchResult>("/search?sort=newest&page_size=6") });

  return (
    <div className="space-y-12">
      <section className="rounded-3xl bg-gradient-to-br from-brand-700 to-brand-500 px-6 py-14 text-center text-white md:px-12">
        <h1 className="mx-auto max-w-3xl text-3xl font-extrabold tracking-tight md:text-5xl">Find a place for exactly the time you need</h1>
        <p className="mx-auto mt-3 max-w-2xl text-brand-50">Check in at any hour. Stay for 24, 48, 72 hours or more. See real availability before you ask.</p>
        <div className="mx-auto mt-8 max-w-5xl text-left text-ink">
          <SearchBar initial={EMPTY_SEARCH} onSearch={(v) => navigate(`/search?${searchToParams(v)}`)} />
        </div>
      </section>

      {data && data.items.length > 0 && (
        <section>
          <div className="mb-4 flex items-end justify-between">
            <h2 className="text-xl font-bold">Recently added</h2>
            <Link to="/search" className="text-sm font-medium text-brand-700 hover:underline">See all places</Link>
          </div>
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {data.items.map((item) => <PropertyCard key={item.id} item={item} carry="" />)}
          </div>
        </section>
      )}

      {!user && (
        <section className="rounded-2xl border border-gray-200 bg-white p-8 text-center">
          <h2 className="text-xl font-bold">Have a place to share?</h2>
          <p className="mt-1 text-gray-600">Become a host, set your own price, and decide who stays.</p>
          <Link to="/register" className="mt-4 inline-block rounded-lg bg-brand-600 px-5 py-2 text-sm font-medium text-white hover:bg-brand-700">Sign up to host</Link>
        </section>
      )}
    </div>
  );
}
