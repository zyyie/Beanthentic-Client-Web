const iconButtons = document.querySelectorAll(".mini-icon-item[data-target]");
const infoPanels = document.querySelectorAll(".info-panel");

iconButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const targetId = button.dataset.target;

    iconButtons.forEach((item) => item.classList.remove("active"));
    button.classList.add("active");

    infoPanels.forEach((panel) => {
      panel.classList.toggle("active", panel.id === targetId);
    });
  });
});
