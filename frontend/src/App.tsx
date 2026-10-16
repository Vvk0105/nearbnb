import { useQuery } from "@tanstack/react-query";

async function fetchHealth(): Promise<{ status: string }> {
  const res = await fetch("/api/v1/health");
  if (!res.ok) throw new Error("API unreachable");
  return res.json();
}

export default function App() {
  const { data, isLoading, isError } = useQuery({ queryKey: ["health"], queryFn: fetchHealth });

  return (
    <main className="mx-auto max-w-xl p-8">
      <h1 className="text-3xl font-bold">NearBNB</h1>
      <p className="mt-2 text-gray-600">Phase 0 skeleton</p>
      <p className="mt-6">
        API status:{" "}
        <strong>{isLoading ? "checking..." : isError ? "unreachable" : data?.status}</strong>
      </p>
    </main>
  );
}
