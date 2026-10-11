/* First timers per week or month, stacked by service. */
(function () {
  const data = window.BCI_ARRIVALS;
  const el = document.getElementById("arrivals");
  if (!data || !el || !window.Chart) return;

  const INK = "#131a3a", INK2 = "#434c72", GRID = "#e9ecf6", SURFACE = "#ffffff";
  const series = [
    { key: "sunday", label: "Sunday service", color: "#2f4beb" },
    { key: "thursday", label: window.BCI_THURSDAY_NAME || "Dominion Service", color: "#a8821f" },
    { key: "other", label: "Other events", color: "#1f9e8f" },
  ];

  // Round only the top of each column; segments below it stay square.
  function isTop(ctx) {
    const i = ctx.dataIndex;
    for (let d = series.length - 1; d >= 0; d--) {
      if ((data[series[d].key][i] || 0) > 0) return d === ctx.datasetIndex;
    }
    return false;
  }

  Chart.defaults.font.family = '"Atkinson Hyperlegible", "Segoe UI", system-ui, sans-serif';
  Chart.defaults.font.size = 13;
  Chart.defaults.color = INK2;

  new Chart(el, {
    type: "bar",
    data: {
      labels: data.labels,
      datasets: series.map((s) => ({
        label: s.label,
        data: data[s.key],
        backgroundColor: s.color,
        hoverBackgroundColor: s.color,
        borderColor: SURFACE,
        borderWidth: { top: 2, right: 0, bottom: 0, left: 0 },
        borderSkipped: "start",
        borderRadius: (ctx) => (isTop(ctx) ? { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 } : 0),
        maxBarThickness: 24,
        categoryPercentage: 0.7,
      })),
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 400 },
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: SURFACE,
          titleColor: INK,
          bodyColor: INK2,
          footerColor: INK,
          borderColor: "#dce1f0",
          borderWidth: 1,
          padding: 10,
          boxPadding: 4,
          usePointStyle: true,
          callbacks: {
            title: (items) => (data.mode === "week" ? "Week of " : "") + items[0].label,
            label: (item) => ` ${item.dataset.label}: ${item.raw}`,
            footer: (items) => "Total: " + items.reduce((a, b) => a + (b.raw || 0), 0),
          },
          filter: (item) => item.raw > 0,
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { display: false },
          border: { color: "#c9d0e6" },
          ticks: { maxRotation: 0, autoSkipPadding: 12 },
        },
        y: {
          stacked: true,
          beginAtZero: true,
          grid: { color: GRID, lineWidth: 1 },
          border: { display: false },
          ticks: { precision: 0, maxTicksLimit: 6 },
        },
      },
    },
  });
})();
