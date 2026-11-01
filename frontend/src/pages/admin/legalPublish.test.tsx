import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "../../App";
import { AuthProvider } from "../../lib/auth";

const json = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));

const me = {
  id: "1", email: "admin@example.com", full_name: "Ada Admin", is_admin: true, phone: null, dob: null, photo_url: null, status: "active", email_verified: true,
  totp_enabled: false, pending_email: null, last_login_at: null, social_links: [], pending_consents: [], created_at: null,
};

let posts: { url: string; body: { title: string; body: string } }[] = [];
function open() {
  posts = [];
  vi.stubGlobal("fetch", (url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/me")) return json(200, me);
    if (url.includes("/v1/admin/legal/") && init?.method === "POST") {
      posts.push({ url, body: JSON.parse(String(init.body)) });
      return json(200, { version: 2 });
    }
    return json(404, { error: { code: "NOT_FOUND", message: "not found" } });
  });
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/admin/legal"]}><AuthProvider><App /></AuthProvider></MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("publishing a legal document", () => {
  it("starts with the usual title of the document, follows the document picked, and resets after publishing", async () => {
    open();
    const u = userEvent.setup();
    const title = (await screen.findByLabelText("Title")) as HTMLInputElement;
    expect(title.value).toBe("Terms of Service");

    await u.selectOptions(screen.getByRole("combobox"), "privacy");
    expect(title.value).toBe("Privacy Policy");

    await u.type(screen.getByLabelText("Full text"), "Our promise about your details.");
    await u.click(screen.getByRole("button", { name: "Publish new version" }));
    expect(await screen.findByText(/Published version 2/)).toBeTruthy();
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toContain("/v1/admin/legal/privacy");
    expect(posts[0].body).toEqual({ title: "Privacy Policy", body: "Our promise about your details." });

    expect((screen.getByLabelText("Full text") as HTMLTextAreaElement).value).toBe("");   // text cleared
    expect(title.value).toBe("Privacy Policy");                                          // title back to the usual one
  });

  it("a title typed by hand is sent, then the box returns to the usual title", async () => {
    open();
    const u = userEvent.setup();
    const title = (await screen.findByLabelText("Title")) as HTMLInputElement;
    await u.clear(title);
    await u.type(title, "Terms of Service (2026)");
    await u.type(screen.getByLabelText("Full text"), "Text.");
    await u.click(screen.getByRole("button", { name: "Publish new version" }));
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].body.title).toBe("Terms of Service (2026)");
    await waitFor(() => expect(title.value).toBe("Terms of Service"));
  });
});
