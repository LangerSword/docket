(function () {
  "use strict";

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;

  /* Mobile menu */
  var menuButton = document.getElementById("menu-button");
  var siteNav = document.getElementById("site-nav");
  if (menuButton && siteNav) {
    var setMenu = function (open) {
      siteNav.classList.toggle("is-open", open);
      menuButton.setAttribute("aria-expanded", open ? "true" : "false");
      menuButton.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    };
    menuButton.addEventListener("click", function () {
      setMenu(menuButton.getAttribute("aria-expanded") !== "true");
    });
    siteNav.addEventListener("click", function (event) {
      if (event.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setMenu(false);
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 860) setMenu(false);
    });
  }

  /* Clean anchors, smooth scroll and active section */
  var navLinks = Array.prototype.slice.call(document.querySelectorAll('.site-nav a[href^="#"]'));
  var cleanHash = function () {
    if (window.location.hash) {
      history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  };

  document.addEventListener("click", function (event) {
    var link = event.target.closest('a[href^="#"]');
    if (!link) return;
    var id = link.getAttribute("href");
    if (!id || id === "#") return;
    var target = document.querySelector(id);
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "start" });
    history.replaceState(null, "", window.location.pathname + window.location.search);
    if (window.innerWidth <= 860 && siteNav) {
      siteNav.classList.remove("is-open");
      if (menuButton) menuButton.setAttribute("aria-expanded", "false");
    }
  }, false);

  if (window.location.hash) {
    var initial = document.querySelector(window.location.hash);
    if (initial) {
      window.requestAnimationFrame(function () {
        initial.scrollIntoView({ behavior: "auto", block: "start" });
        cleanHash();
      });
    }
  }

  if ("IntersectionObserver" in window && navLinks.length) {
    var byId = {};
    navLinks.forEach(function (link) { byId[link.getAttribute("href").slice(1)] = link; });
    var sectionObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        navLinks.forEach(function (link) { link.removeAttribute("aria-current"); });
        var link = byId[entry.target.id];
        if (link) link.setAttribute("aria-current", "true");
      });
    }, { rootMargin: "-30% 0px -58% 0px", threshold: 0 });
    Object.keys(byId).forEach(function (id) {
      var section = document.getElementById(id);
      if (section) sectionObserver.observe(section);
    });
  }

  /* Tabs: hero cases and install code */
  function setupTabs(tablist, onSelect) {
    if (!tablist) return;
    var tabs = Array.prototype.slice.call(tablist.querySelectorAll('[role="tab"]'));
    if (!tabs.length) return;

    function select(tab, moveFocus) {
      tabs.forEach(function (item) {
        var active = item === tab;
        item.setAttribute("aria-selected", active ? "true" : "false");
        item.tabIndex = active ? 0 : -1;
        var panel = document.getElementById(item.getAttribute("aria-controls"));
        if (panel) {
          panel.hidden = !active;
          if (active && !reduceMotion) {
            panel.classList.remove("is-entering");
            void panel.offsetWidth;
            panel.classList.add("is-entering");
            window.setTimeout(function () { panel.classList.remove("is-entering"); }, 400);
          }
        }
      });
      if (moveFocus) tab.focus();
      if (onSelect) onSelect(tab);
    }

    tabs.forEach(function (tab, index) {
      tab.addEventListener("click", function () { select(tab, false); });
      tab.addEventListener("keydown", function (event) {
        var next = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") next = tabs[(index + 1) % tabs.length];
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = tabs[(index - 1 + tabs.length) % tabs.length];
        if (event.key === "Home") next = tabs[0];
        if (event.key === "End") next = tabs[tabs.length - 1];
        if (next) {
          event.preventDefault();
          select(next, true);
        }
      });
    });

    select(tabs.filter(function (tab) { return tab.getAttribute("aria-selected") === "true"; })[0] || tabs[0], false);
  }

  setupTabs(document.querySelector(".case-tabs"));
  setupTabs(document.querySelector(".code-tabs"));

  /* Copy buttons */
  document.querySelectorAll(".copy[data-copy-target]").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.getElementById(button.getAttribute("data-copy-target"));
      if (!target) return;
      var text = target.innerText;
      var done = function () {
        var old = button.textContent;
        button.textContent = "Copied";
        window.setTimeout(function () { button.textContent = old; }, 1400);
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

  /* Paper tilt: physical response to a fine pointer, never on touch */
  var heroArt = document.getElementById("hero-art");
  var paper = heroArt ? heroArt.querySelector(".paper") : null;
  if (paper && finePointer && !reduceMotion) {
    var raf = 0;
    var tilt = function (event) {
      if (raf) return;
      raf = window.requestAnimationFrame(function () {
        raf = 0;
        var box = heroArt.getBoundingClientRect();
        var x = (event.clientX - box.left) / box.width - .5;
        var y = (event.clientY - box.top) / box.height - .5;
        paper.style.setProperty("--ry", (x * 3.2).toFixed(2) + "deg");
        paper.style.setProperty("--rx", (-y * 2.4).toFixed(2) + "deg");
      });
    };
    heroArt.addEventListener("pointermove", tilt);
    heroArt.addEventListener("pointerleave", function () {
      paper.style.setProperty("--ry", "0deg");
      paper.style.setProperty("--rx", "0deg");
    });
  }

  /* Reveal sections without hiding them when JS is unavailable */
  var reveals = document.querySelectorAll(".reveal");
  if (reduceMotion || !("IntersectionObserver" in window)) {
    reveals.forEach(function (item) { item.classList.add("is-in"); });
  } else {
    var revealObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-in");
          revealObserver.unobserve(entry.target);
        }
      });
    }, { rootMargin: "0px 0px -7% 0px", threshold: .06 });
    reveals.forEach(function (item) { revealObserver.observe(item); });
  }
})();