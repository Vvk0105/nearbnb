import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { UserMenu } from "./UserMenu";
import type { User } from "../lib/types";

const user = (over: Partial<User> = {}) => ({ id: "1", email: "ann@example.com", full_name: "Ann Guest", is_admin: false, ...over }) as User;
const show = (u: User, onLogout = vi.fn()) => render(<MemoryRouter><UserMenu user={u} hostLabel="Become a host" onLogout={onLogout} /><p>outside</p></MemoryRouter>);
afterEach(cleanup);

describe("account menu", () => {
  it("starts closed, then lists trips, the host area, settings and log out for a guest", async () => {
    const u = userEvent.setup();
    show(user());
    expect(screen.queryByRole("menu")).toBeNull();
    const button = screen.getByRole("button", { name: "Account menu" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    await u.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    const names = screen.getAllByRole("menuitem").map((i) => i.textContent?.trim());
    expect(names).toEqual(["My trips", "Become a host", "Account settings", "Log out"]);
    expect(screen.getByRole("menuitem", { name: "My trips" }).getAttribute("href")).toBe("/account/trips");
    expect(screen.getByRole("menuitem", { name: "Become a host" }).getAttribute("href")).toBe("/host");
    expect(screen.getByText("ann@example.com")).toBeTruthy();
  });

  it("gives an admin the admin dashboard instead of trips and hosting", async () => {
    const u = userEvent.setup();
    show(user({ is_admin: true }));
    await u.click(screen.getByRole("button", { name: "Account menu" }));
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent?.trim())).toEqual(["Admin dashboard", "Account settings", "Log out"]);
    expect(screen.getByRole("menuitem", { name: "Admin dashboard" }).getAttribute("href")).toBe("/admin");
  });

  it("closes with Escape, with a click elsewhere, and after choosing a page", async () => {
    const u = userEvent.setup();
    show(user());
    const open = () => u.click(screen.getByRole("button", { name: "Account menu" }));
    await open();
    await u.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    await open();
    await u.click(screen.getByText("outside"));
    expect(screen.queryByRole("menu")).toBeNull();
    await open();
    await u.click(screen.getByRole("menuitem", { name: "My trips" }));
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  });

  it("logs out through the menu", async () => {
    const u = userEvent.setup();
    const onLogout = vi.fn();
    show(user(), onLogout);
    await u.click(screen.getByRole("button", { name: "Account menu" }));
    await u.click(screen.getByRole("menuitem", { name: "Log out" }));
    expect(onLogout).toHaveBeenCalledOnce();
  });
});
