import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { ScrollToTop } from "./ScrollToTop";

let scrollTo: ReturnType<typeof vi.fn>;
beforeEach(() => { scrollTo = vi.fn(); vi.stubGlobal("scrollTo", scrollTo); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Back() {
  const navigate = useNavigate();
  return <button onClick={() => navigate(-1)}>back</button>;
}

const app = (
  <MemoryRouter initialEntries={["/search"]}>
    <ScrollToTop />
    <Link to="/property/1">open</Link>
    <Link to="/search#top">same page spot</Link>
    <Back />
    <Routes><Route path="*" element={<p>page</p>} /></Routes>
  </MemoryRouter>
);

describe("scroll to top on a new page", () => {
  it("jumps to the top when a new page is opened", async () => {
    render(app);
    scrollTo.mockClear();                                         // the first render is not a navigation
    await userEvent.setup().click(screen.getByRole("link", { name: "open" }));
    expect(scrollTo).toHaveBeenCalledWith(0, 0);
  });

  it("leaves the scroll alone for a link to a spot on the same page and for the Back button", async () => {
    const u = userEvent.setup();
    render(app);
    await u.click(screen.getByRole("link", { name: "open" }));
    scrollTo.mockClear();
    await u.click(screen.getByRole("button", { name: "back" }));
    expect(scrollTo).not.toHaveBeenCalled();
    await u.click(screen.getByRole("link", { name: "same page spot" }));
    expect(scrollTo).not.toHaveBeenCalled();
  });
});
