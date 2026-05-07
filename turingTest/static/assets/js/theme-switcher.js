/**
 * Ciclo de temas visuales (forest, wave, sakura, night).
 */
(function () {
  const themes = ["forest", "wave", "sakura", "night"];
  const themeNames = {
    forest: "Bosque (森)",
    wave: "Ola (波)",
    sakura: "Sakura (桜)",
    night: "Noche (夜)",
  };

  function getCurrentTheme() {
    const raw = localStorage.getItem("ukiyo-theme") || "forest";
    if (!themes.includes(raw)) return "forest";
    return raw;
  }

  function setTheme(themeName) {
    const html = document.documentElement;
    if (themeName === "forest") {
      html.removeAttribute("data-theme");
    } else {
      html.setAttribute("data-theme", themeName);
    }
    localStorage.setItem("ukiyo-theme", themeName);

    const btn = document.getElementById("theme-toggle");
    if (btn) {
      const i = themes.indexOf(themeName);
      const next = themes[(i + 1) % themes.length];
      btn.setAttribute(
        "aria-label",
        `Tema: ${themeNames[themeName]}. Clic para ${themeNames[next]}`,
      );
      btn.title = themeNames[themeName];
    }
  }

  function cycleTheme() {
    const cur = getCurrentTheme();
    const i = themes.indexOf(cur);
    const next = themes[(i + 1) % themes.length];
    setTheme(next);
    const btn = document.getElementById("theme-toggle");
    if (btn) {
      btn.style.transform = "rotate(180deg)";
      setTimeout(() => {
        btn.style.transform = "";
      }, 200);
    }
  }

  function initTheme() {
    setTheme(getCurrentTheme());
  }

  function setupButton() {
    const btn = document.getElementById("theme-toggle");
    if (btn) btn.addEventListener("click", cycleTheme);
  }

  function setupHeaderScroll() {
    const header = document.querySelector(".site-header");
    if (!header) return;
    const onScroll = () => {
      header.classList.toggle("scrolled", window.pageYOffset > 50);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  function onReady() {
    setTheme(getCurrentTheme());
    setupButton();
    setupHeaderScroll();
  }

  initTheme();
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", onReady);
  } else {
    onReady();
  }
})();
