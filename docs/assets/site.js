(function () {
  "use strict";

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var gsap = window.gsap;
  var ScrollTrigger = window.ScrollTrigger;
  if (gsap && ScrollTrigger) gsap.registerPlugin(ScrollTrigger);

  /* menu */
  var menu = document.getElementById("menu-button");
  var nav = document.getElementById("site-nav");
  if (menu && nav) {
    var setMenu = function (open) {
      nav.classList.toggle("is-open", open);
      menu.setAttribute("aria-expanded", open ? "true" : "false");
      menu.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    };
    menu.addEventListener("click", function () {
      setMenu(menu.getAttribute("aria-expanded") !== "true");
    });
    nav.addEventListener("click", function (event) {
      if (event.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setMenu(false);
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 820) setMenu(false);
    });
  }

  /* clean anchors, no hashes left behind */
  document.addEventListener("click", function (event) {
    var link = event.target.closest('a[href^="#"]');
    if (!link) return;
    var href = link.getAttribute("href");
    if (!href || href === "#") return;
    var target = document.querySelector(href);
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
    history.replaceState(null, "", window.location.pathname + window.location.search);
  }, false);

  if (window.location.hash) {
    var initial = document.querySelector(window.location.hash);
    if (initial) {
      window.requestAnimationFrame(function () {
        initial.scrollIntoView({ behavior: "auto", block: "start" });
        history.replaceState(null, "", window.location.pathname + window.location.search);
      });
    }
  }

  /* install tabs */
  var tablist = document.querySelector(".code-tabs");
  if (tablist) {
    var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));
    var choose = function (tab) {
      tabs.forEach(function (item) {
        var active = item === tab;
        item.setAttribute("aria-selected", active ? "true" : "false");
        item.tabIndex = active ? 0 : -1;
        var panel = document.getElementById(item.getAttribute("aria-controls"));
        if (panel) panel.hidden = !active;
      });
    };
    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { choose(tab); });
      tab.addEventListener("keydown", function (event) {
        var next = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") next = tabs[(index + 1) % tabs.length];
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = tabs[(index - 1 + tabs.length) % tabs.length];
        if (event.key === "Home") next = tabs[0];
        if (event.key === "End") next = tabs[tabs.length - 1];
        if (next) { event.preventDefault(); choose(next); next.focus(); }
      });
    });
  }

  /* copy */
  document.querySelectorAll(".copy[data-copy-target]").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.getElementById(button.getAttribute("data-copy-target"));
      if (!target) return;
      var text = target.innerText;
      var done = function () {
        var old = button.textContent;
        button.textContent = "Copied";
        window.setTimeout(function () { button.textContent = old; }, 1300);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
      } else {
        var area = document.createElement("textarea");
        area.value = text;
        area.setAttribute("readonly", "");
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        try { document.execCommand("copy"); done(); } catch (error) {}
        document.body.removeChild(area);
      }
    });
  });

  /* the cover enters like a file being opened */
  if (gsap && !reduce) {
    var intro = gsap.timeline({ defaults: { ease: "power4.out" } });
    intro
      .from(".refused", { clipPath: "inset(100% 0 0 0)", y: 34, duration: 1.05 }, 0)
      .from(".cover-file", { rotate: 8, y: 54, opacity: 0, duration: 1.1 }, 0.08)
      .from(".cover-copy .micro, .cover-line, .cover-lede, .cover-actions", {
        y: 16, opacity: 0, duration: 0.75, stagger: 0.07
      }, 0.18)
      .from(".stamp", { scale: 1.7, rotate: -18, opacity: 0, duration: 0.45, ease: "back.out(2)" }, 0.78);
  }

  /* the horizontal evidence reel */
  if (gsap && ScrollTrigger) {
    var media = gsap.matchMedia();
    media.add("(min-width: 1001px) and (prefers-reduced-motion: no-preference)", function () {
      var reel = document.querySelector(".reel");
      var track = document.querySelector(".reel-track");
      var panels = Array.prototype.slice.call(document.querySelectorAll(".reel-panel"));
      var progressLine = document.querySelector(".progress-line i");
      var progressNums = Array.prototype.slice.call(document.querySelectorAll(".progress-num"));
      if (!reel || !track) return;

      reel.classList.add("is-horizontal");
      var distance = function () {
        return Math.max(0, track.scrollWidth - window.innerWidth);
      };
      var tween = gsap.to(track, {
        x: function () { return -distance(); },
        ease: "none",
        scrollTrigger: {
          trigger: reel,
          start: "top top",
          end: function () { return "+=" + distance(); },
          pin: true,
          scrub: 1,
          anticipatePin: 1,
          invalidateOnRefresh: true,
          onUpdate: function (self) {
            if (progressLine) progressLine.style.width = (self.progress * 100).toFixed(2) + "%";
            var active = Math.min(progressNums.length - 1, Math.floor(self.progress * progressNums.length));
            progressNums.forEach(function (item, index) {
              item.classList.toggle("is-active", index === active);
            });
          }
        }
      });

      panels.forEach(function (panel) {
        ScrollTrigger.create({
          trigger: panel,
          containerAnimation: tween,
          start: "left 72%",
          onEnter: function () { panel.classList.add("is-live"); },
          onLeaveBack: function () { panel.classList.remove("is-live"); }
        });
      });

      return function () {
        reel.classList.remove("is-horizontal");
        gsap.set(track, { clearProps: "transform" });
        if (progressLine) progressLine.style.width = "0%";
      };
    });
  }

  /* keep content visible without JS; animate only the transform */
  var reveals = document.querySelectorAll(".reveal");
  if (reduce || !("IntersectionObserver" in window)) {
    reveals.forEach(function (item) { item.classList.add("is-in"); });
  } else {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-in");
          observer.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: .06 });
    reveals.forEach(function (item) { observer.observe(item); });
  }

  if (document.fonts && document.fonts.ready && window.ScrollTrigger) {
    document.fonts.ready.then(function () { window.ScrollTrigger.refresh(); });
  }
})();