document.addEventListener("DOMContentLoaded", () => {
  const menu = document.getElementById("mobileMenu");
  const nav = document.getElementById("mainNav");
  if (menu && nav) menu.addEventListener("click", () => {
    const opened = nav.classList.toggle("nav-open");
    menu.setAttribute("aria-expanded", String(opened));
  });

  const carousel = document.getElementById("resourceCarousel");
  const progress = document.getElementById("carouselProgress");
  if (carousel) {
    const step = () => {
      const card = carousel.querySelector(".resource-card");
      return card ? card.getBoundingClientRect().width + 18 : 280;
    };
    document.querySelector(".carousel-next")?.addEventListener("click", () => {
      if (carousel.scrollLeft + carousel.clientWidth >= carousel.scrollWidth - 8) {
        carousel.scrollTo({left: 0, behavior: "smooth"});
      } else carousel.scrollBy({left: step(), behavior: "smooth"});
    });
    document.querySelector(".carousel-prev")?.addEventListener("click", () => {
      if (carousel.scrollLeft <= 8) carousel.scrollTo({left: carousel.scrollWidth, behavior: "smooth"});
      else carousel.scrollBy({left: -step(), behavior: "smooth"});
    });
    const updateProgress = () => {
      if (!progress) return;
      const max = carousel.scrollWidth - carousel.clientWidth;
      progress.style.width = (max <= 0 ? 100 : Math.max(12, carousel.scrollLeft / max * 100)) + "%";
    };
    carousel.addEventListener("scroll", updateProgress, {passive: true});
    window.addEventListener("resize", updateProgress);
    updateProgress();
    let autoplay;
    const start = () => {
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
      stop();
      autoplay = setInterval(() => {
        if (!document.hidden) {
          const atEnd = carousel.scrollLeft + carousel.clientWidth >= carousel.scrollWidth - 8;
          carousel.scrollTo({left: atEnd ? 0 : carousel.scrollLeft + step(), behavior: "smooth"});
        }
      }, 4800);
    };
    const stop = () => { if (autoplay) clearInterval(autoplay); };
    carousel.addEventListener("mouseenter", stop);
    carousel.addEventListener("mouseleave", start);
    carousel.addEventListener("focusin", stop);
    carousel.addEventListener("focusout", start);
    start();
  }

  document.querySelectorAll(".toggle-password").forEach(button => {
    button.addEventListener("click", () => {
      const input = document.getElementById(button.dataset.target);
      if (!input) return;
      const showing = input.type === "password";
      input.type = showing ? "text" : "password";
      button.textContent = showing ? "Hide" : "Show";
    });
  });

  const modal = document.getElementById("bookingModal");
  if (modal) {
    const close = () => { modal.classList.remove("is-open"); modal.setAttribute("aria-hidden", "true"); document.body.classList.remove("modal-open"); };
    document.querySelectorAll(".open-booking").forEach(button => button.addEventListener("click", () => {
      document.getElementById("bookingResourceId").value = button.dataset.resource;
      document.getElementById("bookingResourceName").textContent = button.dataset.name + " · Submit a request for review.";
      document.getElementById("bookingTitle").textContent = "Book " + button.dataset.name;
      modal.classList.add("is-open");
      modal.setAttribute("aria-hidden", "false");
      document.body.classList.add("modal-open");
      const dateInput = document.getElementById("bookingDate");
      const now = new Date();
      // local calendar date (toISOString() would use UTC and can be a day off)
      dateInput.min = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
      dateInput.value = "";
      resetSlots();
      dateInput.focus();
    }));

    // Live availability: disable time slots already taken for the chosen resource/date.
    const slotSelect = document.getElementById("bookingSlot");
    const slotNote = document.getElementById("slotNote");
    const resetSlots = () => {
      if (!slotSelect) return;
      slotSelect.value = "";
      slotSelect.querySelectorAll("option").forEach(o => { o.disabled = false; o.textContent = o.value || o.textContent; });
      if (slotNote) slotNote.textContent = "Pick a date to see which slots are free.";
    };
    document.getElementById("bookingDate")?.addEventListener("change", async event => {
      const resourceId = document.getElementById("bookingResourceId").value;
      if (!slotSelect || !event.target.value || !resourceId) return;
      try {
        const response = await fetch("/api/availability?resource_id=" + encodeURIComponent(resourceId) +
                                     "&date=" + encodeURIComponent(event.target.value), {headers: {"Accept": "application/json"}});
        if (!response.ok) throw new Error("availability unavailable");
        const data = await response.json();
        let freeCount = 0, totalCount = 0;
        slotSelect.querySelectorAll("option").forEach(option => {
          if (!option.value) return;
          totalCount += 1;
          const taken = data.taken.includes(option.value);
          option.disabled = taken;
          option.textContent = option.value + (taken ? " (already requested)" : "");
          if (!taken) freeCount += 1;
        });
        if (slotSelect.selectedOptions[0]?.disabled) slotSelect.value = "";
        if (slotNote) slotNote.textContent = freeCount
          ? freeCount + " of " + totalCount + " slots are free on this date."
          : "No free slots on this date. Try another day.";
      } catch (error) {
        if (slotNote) slotNote.textContent = "Couldn't check availability. You can still submit; conflicts are checked on the server.";
      }
    });
    document.getElementById("closeBooking")?.addEventListener("click", close);
    modal.addEventListener("click", e => { if (e.target === modal) close(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });
  }

  document.querySelectorAll(".reveal").forEach(el => el.classList.add("will-reveal"));
  if ("IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (entry.isIntersecting) { entry.target.classList.add("revealed"); observer.unobserve(entry.target); }
    }), {threshold: 0.08});
    document.querySelectorAll(".will-reveal").forEach(el => observer.observe(el));
  } else document.querySelectorAll(".will-reveal").forEach(el => el.classList.add("revealed"));

  document.querySelectorAll(".flash").forEach(flash => {
    setTimeout(() => { if (flash.isConnected) flash.classList.add("flash-fade"); }, 6500);
  });
});
