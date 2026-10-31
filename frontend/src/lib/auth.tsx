import { createContext, useContext, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./api";
import type { User } from "./types";

type AuthState = {
  user: User | null;
  loading: boolean;
  setUser: (user: User | null) => void;
  logout: () => Promise<void>;
  /** Who is signed in right now, read from the store itself. `user` reaches the screen a moment later, so right after
   *  logging out it can still show the old person: pages that redirect on it (login, sign up) ask this instead. */
  signedInNow: () => boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery<User | null>({
    queryKey: ["me"],
    retry: false,
    staleTime: 60_000,
    queryFn: async () => {
      try {
        return await api<User>("/me");
      } catch (e) {
        if (e instanceof ApiError && (e.status === 401 || e.status === 403)) return null;
        throw e;
      }
    },
  });

  // Drops everything cached for the previous user (sessions, admin lists, ...) but NEVER the "me" query itself.
  // queryClient.clear() would detach this provider's live subscription, so later updates (the next login)
  // would be written to the cache but never reach the screen.
  const forgetOtherUsersData = () =>
    queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });

  const value: AuthState = {
    user: data ?? null,
    loading: isLoading,
    setUser: (user) => {
      const previous = queryClient.getQueryData<User | null>(["me"]);
      if (previous?.id !== user?.id) forgetOtherUsersData();
      queryClient.setQueryData(["me"], user);
    },
    signedInNow: () => Boolean(queryClient.getQueryData<User | null>(["me"])),
    logout: async () => {
      try {
        await api("/auth/logout", { method: "POST" });
      } finally {
        forgetOtherUsersData();
        queryClient.setQueryData(["me"], null);
      }
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
