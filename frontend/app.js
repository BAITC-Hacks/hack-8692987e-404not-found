const $ = (id) => document.getElementById(id);

function setMetrics(result) {
  const summary = result.summary || {};
  $("campaign-count").textContent = result.campaigns?.length ?? "—";
  $("score").textContent = summary.net_score?.toFixed(0) ?? "—";
  $("contacts").textContent = summary.contacts ?? "—";
  $("budget").textContent = summary.budget?.toFixed(0) ?? "—";
  $("pilots").textContent = result.pilots_used ?? "—";
  $("data-source").textContent = result.data_source === "uploaded_csv"
    ? `Загруженный профиль: ${result.profile_rows} строк.`
    : `Демо-профиль: ${result.profile_rows} строк.`;
  $("limits").textContent = summary.within_limits ? "Ограничения соблюдены" : "Есть превышение";
  $("limits").style.color = summary.within_limits ? "#8df0bd" : "#ff9f9f";
  $("limits").style.background = summary.within_limits ? "#12352a" : "#4a2028";
}

function renderCampaigns(campaigns) {
  const body = $("campaigns");
  if (!campaigns?.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">Кампании отсутствуют</td></tr>';
    return;
  }
  const cell = (value) => {
    const element = document.createElement("td");
    element.textContent = value || "—";
    return element;
  };
  body.replaceChildren(...campaigns.map((campaign) => {
    const row = document.createElement("tr");
    [
      campaign.campaign_name,
      campaign.filter_arpu_segment,
      campaign.filter_data_segment,
      campaign.filter_call_segment,
      campaign.filter_current_tariff,
      campaign.target_tariff,
      campaign.channel,
    ].forEach((value) => row.appendChild(cell(value)));
    return row;
  }));
}

async function runAgent() {
  const button = $("run-button");
  button.disabled = true;
  button.textContent = "Запускаем...";
  $("status").textContent = "Агент выполняет пилоты и оптимизирует кампании...";
  try {
    const response = await fetch("/api/run", { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Ошибка сервера");
    setMetrics(result);
    renderCampaigns(result.campaigns);
    $("status").textContent = "Готово: кампании рассчитаны локальным окружением.";
  } catch (error) {
    $("status").textContent = `Ошибка: ${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = "Запустить агента";
  }
}

async function loadStatus() {
  try {
    const response = await fetch("/api/status");
    const status = await response.json();
    $("status").textContent = status.message;
    $("data-source").textContent = status.data_source === "uploaded_csv"
      ? `Загруженный профиль: ${status.profile_rows} строк.`
      : "Демо-профиль будет использован по умолчанию.";
  } catch {
    $("status").textContent = "Сервис недоступен";
  }

  async function loadDataset() {
    try {
      const response = await fetch("/api/dataset");
      const data = await response.json();
      document.getElementById("channels").replaceChildren(...data.channels.map((channel) => {
        const row = document.createElement("div");
        row.className = "info-row";
        row.textContent = `${channel.name} · ${channel.cost} у.е. · ×${channel.effectiveness.toFixed(2)}`;
        return row;
      }));
      document.getElementById("dataset").replaceChildren(...Object.entries(data.tables).map(([name, count]) => {
        const row = document.createElement("div");
        row.className = "info-row";
        row.textContent = `${name} · ${count.toLocaleString("ru-RU")} строк`;
        return row;
      }));
    } catch {
      document.getElementById("dataset").textContent = "Не удалось загрузить сведения о БД";
    }
  }
}

$("run-button").addEventListener("click", runAgent);
document.getElementById("profile-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  $("status").textContent = "Загружаем профиль клиентов...";
  try {
    const response = await fetch("/api/profile", { method: "POST", body: form });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Не удалось загрузить профиль");
    $("status").textContent = `${result.message}: ${result.rows} строк.`;
    $("data-source").textContent = `Загруженный профиль: ${result.rows} строк.`;
  } catch (error) {
    $("status").textContent = `Ошибка загрузки: ${error.message}`;
  }
});
loadStatus();
loadDataset();
