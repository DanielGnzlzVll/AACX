(function () {
  const WARNING_SECONDS = 10;
  const endsAt = new WeakMap();

  function tick() {
    const now = performance.now();
    for (const timer of document.querySelectorAll("[data-seconds-left]")) {
      if (!endsAt.has(timer)) {
        endsAt.set(timer, now + Number(timer.dataset.secondsLeft) * 1000);
      }
      const left = Math.max(Math.ceil((endsAt.get(timer) - now) / 1000), 0);
      timer.textContent = left + " s";
      timer.classList.toggle("is-warning", left < WARNING_SECONDS);
    }
  }

  setInterval(tick, 250);
})();
