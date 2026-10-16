import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../lib/api";
import { Alert, Card, errorMessage } from "../components/ui";

type Doc = { doc_type: string; version: number; title: string; body: string; published_at: string };

export default function LegalPage() {
  const { docType = "terms" } = useParams();
  const { data, error, isLoading } = useQuery({ queryKey: ["legal", docType], queryFn: () => api<Doc>(`/legal/${docType}`), retry: false });
  if (isLoading) return <p className="text-gray-500">Loading...</p>;
  if (error) return <Alert>{errorMessage(error)}</Alert>;
  return (
    <Card title={data!.title}>
      <p className="mb-4 text-xs text-gray-500">
        Version {data!.version} · published {new Date(data!.published_at).toLocaleDateString()}
      </p>
      <div className="whitespace-pre-wrap text-sm leading-6">{data!.body}</div>
    </Card>
  );
}
