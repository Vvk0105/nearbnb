import { Link } from "react-router-dom";
import { useAuth } from "../lib/auth";

export default function Home() {
  const { user } = useAuth();
  return (
    <div className="py-10 text-center">
      <h1 className="text-4xl font-bold">Find a place for exactly the time you need</h1>
      <p className="mt-3 text-gray-600">Flexible check-in, stays in 24-hour blocks. Search and booking arrive in the next phases.</p>
      {!user && (
        <div className="mt-6 flex justify-center gap-3">
          <Link to="/register" className="rounded-lg bg-rose-600 px-4 py-2 text-white">
            Sign up
          </Link>
          <Link to="/login" className="rounded-lg border border-gray-300 px-4 py-2">
            Log in
          </Link>
        </div>
      )}
    </div>
  );
}
