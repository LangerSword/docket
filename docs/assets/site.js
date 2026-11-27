(function () {
  "use strict";
  document.documentElement.classList.add("js");

  var menu = document.getElementById("menu");
  var links = document.getElementById("nav-links");
  if (menu && links) {
    menu.addEventListener("click", function () {
      var open = links.classList.toggle("open");
      menu.setAttribute("aria-expanded", open ? "true" : "false");
      menu.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    });
    links.addEventListener("click", function (event) {
      if (event.target.closest("a")) {
        links.classList.remove("open");
        menu.setAttribute("aria-expanded", "false");
      }
    });
  }

  document.querySelectorAll(".copy").forEach(function (button) {
    button.addEventListener("click", function () {
      var block = button.closest(".code-block");
      var code = block ? block.querySelector("pre") : null;
      if (!code) return;
      var text = code.innerText;
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

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var items = document.querySelectorAll(".reveal");
  if (reduce || !("IntersectionObserver" in window)) {
    items.forEach(function (item) { item.classList.add("in"); });
    return;
  }
  var observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        observer.unobserve(entry.target);
      }
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  items.forEach(function (item) { observer.observe(item); });
})();