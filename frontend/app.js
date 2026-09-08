/* BIS Homepage — app.js */

(function () {
  "use strict";

  const nav        = document.getElementById("main-nav");
  const hamburger  = document.getElementById("hamburger-btn");
  const mobileMenu = document.getElementById("mobile-menu");

  // ── Mobile menu ────────────────────────────────────────────────
  function setMenu(open) {
    if (!mobileMenu || !hamburger) return;
    mobileMenu.classList.toggle("open", open);
    hamburger.setAttribute("aria-expanded", String(open));
  }

  hamburger?.addEventListener("click", () => {
    setMenu(!mobileMenu.classList.contains("open"));
  });

  // Close after tapping a link, on Escape, or when clicking outside
  mobileMenu?.addEventListener("click", (e) => {
    if (e.target.closest("a")) setMenu(false);
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setMenu(false);
  });

  document.addEventListener("click", (e) => {
    if (!mobileMenu?.classList.contains("open")) return;
    if (!e.target.closest("#mobile-menu") && !e.target.closest("#hamburger-btn")) {
      setMenu(false);
    }
  });

  // ── Nav elevation on scroll ────────────────────────────────────
  let ticking = false;
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      nav?.classList.toggle("scrolled", window.scrollY > 24);
      ticking = false;
    });
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  // ── Reveal on scroll ───────────────────────────────────────────
  const revealables = document.querySelectorAll(".reveal");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduceMotion || !("IntersectionObserver" in window)) {
    revealables.forEach((el) => el.classList.add("is-visible"));
  } else {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry, i) => {
          if (!entry.isIntersecting) return;
          const el = entry.target;
          el.style.transitionDelay = Math.min(i * 70, 280) + "ms";
          el.classList.add("is-visible");
          io.unobserve(el);
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.12 }
    );
    revealables.forEach((el) => io.observe(el));
  }

  // ── Only one FAQ item open at a time ──────────────────────────
  const faqItems = document.querySelectorAll(".faq-item");
  faqItems.forEach((item) => {
    item.addEventListener("toggle", () => {
      if (!item.open) return;
      faqItems.forEach((other) => {
        if (other !== item) other.open = false;
      });
    });
  });
})();
