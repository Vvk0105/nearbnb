import { useEffect } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

/**
 * A single-page app keeps the scroll position when the page changes, so a property opened from far down the search
 * results would start half way down. Jump to the top whenever a new page is opened. Going Back is left to the browser,
 * and a link to a spot on the same page (#availability) is left alone.
 */
export function ScrollToTop() {
  const { pathname, hash } = useLocation();
  const type = useNavigationType();
  useEffect(() => {
    if (type === "POP" || hash) return;
    window.scrollTo(0, 0);
  }, [pathname]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}
