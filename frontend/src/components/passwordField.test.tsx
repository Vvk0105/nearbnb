import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../App";
import { AuthProvider } from "../lib/auth";
import { Field } from "./ui";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("password box", () => {
  it("hides what is typed, and the eye button shows and hides it again", async () => {
    const u = userEvent.setup();
    render(<Field id="pw" label="Password" type="password" defaultValue="" />);
    const input = screen.getByLabelText("Password") as HTMLInputElement;
    await u.type(input, "Sup3r-secret");
    expect(input.type).toBe("password");
    const eye = screen.getByRole("button", { name: "Show password" });
    expect(eye.getAttribute("aria-pressed")).toBe("false");
    await u.click(eye);
    expect(input.type).toBe("text");
    expect(input.value).toBe("Sup3r-secret");                        // nothing typed is lost
    expect(screen.getByRole("button", { name: "Hide password" }).getAttribute("aria-pressed")).toBe("true");
    await u.click(screen.getByRole("button", { name: "Hide password" }));
    expect(input.type).toBe("password");
  });

  it("is only added to password boxes", () => {
    render(<Field id="em" label="Email" type="email" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("does not submit the form when the eye is clicked", async () => {
    const onSubmit = vi.fn((e: React.FormEvent) => e.preventDefault());
    render(<form onSubmit={onSubmit}><Field id="pw" label="Password" type="password" /></form>);
    await userEvent.setup().click(screen.getByRole("button", { name: "Show password" }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("is on the login, sign-up and change-password screens", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ error: { code: "NOT_AUTHENTICATED", message: "x" } }), { status: 401, headers: { "Content-Type": "application/json" } })));
    for (const path of ["/login", "/register"]) {
      const { unmount } = render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>
        </QueryClientProvider>,
      );
      expect(await screen.findByRole("button", { name: "Show password" })).toBeTruthy();
      unmount();
    }
  });
});

describe("top bar", () => {
  it("has no host link of its own: visitors see only log in and sign up", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve(new Response(JSON.stringify({ error: { code: "NOT_AUTHENTICATED", message: "x" } }), { status: 401, headers: { "Content-Type": "application/json" } })));
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/"]}><AuthProvider><App /></AuthProvider></MemoryRouter>
      </QueryClientProvider>,
    );
    const header = within(await screen.findByRole("banner"));
    expect(await header.findByRole("link", { name: "Log in" })).toBeTruthy();
    expect(header.getByRole("link", { name: "Sign up" })).toBeTruthy();
    expect(header.queryByRole("link", { name: /host/i })).toBeNull();
  });
});
