/* Away Game Travel Planner front end. Vanilla JS; talks only to the Flask API.
   Sign-in uses Supabase Auth in the browser (publishable key). Demo mode uses fake tokens. */
const CFG = window.APP_CONFIG;
const $ = (sel) => document.querySelector(sel);
const sb = !CFG.demo && window.supabase ? window.supabase.createClient(CFG.supabaseUrl, CFG.supabaseKey) : null;
let demoToken = null;
let state = { me: null, trips: [], current: null, venues: [] };

const money = (n) => Number(n || 0).toLocaleString("en-US", { style: "currency", currency: "USD" });
const when = (s) => (s ? new Date(s).toLocaleString("en-US", { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
const label = (t) => t.replace(/_/g, " ");
const mapLink = (lat, lng) => `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lng}#map=16/${lat}/${lng}`;

function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) node.append(kid.nodeType ? kid : String(kid));
  return node;
}

function flash(msg, kind = "info") {
  const f = $("#flash");
  f.textContent = msg;
  f.className = "flash " + kind;
  f.hidden = !msg;
  if (msg) window.scrollTo({ top: 0, behavior: "smooth" });
}

async function token() {
  if (CFG.demo) return demoToken;
  const { data } = await sb.auth.getSession();
  return data.session ? data.session.access_token : null;
}

async function api(path, opts = {}) {
  const t = await token();
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(t ? { Authorization: "Bearer " + t } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

function show(view) {
  for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== "view-" + view;
}

async function busy(button, fn) {
  const old = button ? button.textContent : "";
  if (button) { button.disabled = true; button.textContent = "Working..."; }
  try { await fn(); } catch (e) { flash(e.message, "error"); }
  finally { if (button) { button.disabled = false; button.textContent = old; } }
}

/* ---------- sign in ---------- */
function setupAuth() {
  if (CFG.demo) {
    $("#demo-box").hidden = false;
    for (const b of document.querySelectorAll("[data-demo]")) {
      b.addEventListener("click", () => { demoToken = b.dataset.demo; boot(); });
    }
    return;
  }
  const form = $("#auth-form");
  form.hidden = false;
  let mode = "signin";
  for (const tab of form.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      mode = tab.dataset.mode;
      form.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", t === tab));
      $("#auth-submit").textContent = mode === "signin" ? "Sign in" : "Create account";
    });
  }
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const { email, password } = Object.fromEntries(new FormData(form));
    busy($("#auth-submit"), async () => {
      const fn = mode === "signin" ? sb.auth.signInWithPassword : sb.auth.signUp;
      const { data, error } = await fn.call(sb.auth, { email, password });
      if (error) throw error;
      if (!data.session) { flash("Check your email to confirm your account, then sign in.", "info"); return; }
      flash("");
      boot();
    });
  });
}

$("#signout").addEventListener("click", async () => {
  if (sb) await sb.auth.signOut();
  demoToken = null;
  state = { me: null, trips: [], current: null, venues: [] };
  $("#whoami").hidden = true;
  flash("");
  show("auth");
});

/* ---------- onboarding ---------- */
function dietBoxes(container, selected = []) {
  container.replaceChildren(...CFG.dietTags.map((t) =>
    el("label", { class: "check" }, el("input", { type: "checkbox", value: t, checked: selected.includes(t) }), " " + label(t))));
}
const checkedDiets = (container) => [...container.querySelectorAll("input:checked")].map((i) => i.value);

function setupOnboard() {
  const form = $("#onboard-form");
  dietBoxes(form.querySelector(".diet-boxes"));
  form.addEventListener("change", () => {
    const role = form.role.value;
    form.querySelectorAll("[data-for]").forEach((d) => (d.hidden = d.dataset.for !== role));
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    data.diet_tags = checkedDiets(form.querySelector(".diet-boxes"));
    busy(form.querySelector("button[type=submit]"), async () => {
      await api("/api/onboard", { method: "POST", body: data });
      flash("");
      boot();
    });
  });
}

/* ---------- boot ---------- */
async function boot() {
  const t = await token();
  if (!t) return show("auth");
  try {
    state.me = await api("/api/me");
  } catch (e) {
    if (e.status === 401) { flash("Please sign in.", "info"); return show("auth"); }
    return flash(e.message, "error");
  }
  $("#whoami").hidden = false;
  const p = state.me.profile;
  $("#who-name").textContent = p ? `${p.full_name} (${p.role})` : state.me.user.email;
  if (!p || !p.team_id) return show("onboard");
  if (p.role === "coach") return coachHome();
  return playerHome();
}

/* ================= COACH ================= */
async function coachHome() {
  show("coach");
  const [team, trips, venues] = await Promise.all([api("/api/team"), api("/api/trips"), api("/api/venues")]);
  state.trips = trips;
  state.venues = venues;
  state.team = team;
  $("#team-name").textContent = team.team.name;
  $("#join-code").textContent = team.team.join_code;
  const needs = [...new Set(team.players.flatMap((p) => p.diet_tags || []))];
  $("#roster-line").textContent = `${team.players.length} players joined` + (needs.length ? ` · needs: ${needs.map(label).join(", ")}` : "");
  $("#venue-select").replaceChildren(...venues.map((v) => el("option", { value: v.id }, v.name)));
  renderTripList();
  if (!state.current && trips.length) openTrip(trips[trips.length - 1].id);
  else if (state.current) openTrip(state.current.id);
}

function renderTripList() {
  const list = $("#trip-list");
  if (!state.trips.length) return list.replaceChildren(el("li", { class: "sub" }, "No trips yet. Create one."));
  list.replaceChildren(...state.trips.map((t) =>
    el("li", {}, el("button", { type: "button", class: "list-btn" + (state.current && state.current.id === t.id ? " active" : ""), onclick: () => openTrip(t.id) },
      el("strong", {}, "vs " + t.opponent), el("span", { class: "sub" }, `${when(t.match_at)} · ${statusText(t.status)}`)))));
}

const statusText = (s) => ({ PLANNING: "Planning", AWAITING_APPROVAL: "Waiting for approval", BOOKED: "Booked", CANCELLED: "Cancelled", COMPLETED: "Completed", IN_PROGRESS: "On the road", DRAFT: "Draft" }[s] || s);

$("#new-trip-btn").addEventListener("click", () => {
  const f = $("#trip-form");
  f.hidden = false;
  f.num_players.value = state.team.players.length || 20;
  $("#trip-detail").replaceChildren();
  f.opponent.focus();
});
$("#trip-cancel").addEventListener("click", () => { $("#trip-form").hidden = true; if (state.current) openTrip(state.current.id); });
$("#trip-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const f = e.target;
  const data = Object.fromEntries(new FormData(f));
  busy(f.querySelector("button[type=submit]"), async () => {
    const trip = await api("/api/trips", { method: "POST", body: data });
    f.hidden = true;
    f.reset();
    state.current = trip;
    state.trips = await api("/api/trips");
    renderTripList();
    renderCoachTrip(trip);
    flash(`Priced ${trip.packages.length} packages for ${trip.opponent}.`, "ok");
  });
});

async function openTrip(id) {
  $("#trip-form").hidden = true;
  try {
    state.current = await api(`/api/trips/${id}`);
    renderTripList();
    renderCoachTrip(state.current);
  } catch (e) { flash(e.message, "error"); }
}

async function tripAction(button, path, body, okMsg) {
  await busy(button, async () => {
    const res = await api(`/api/trips/${state.current.id}/${path}`, { method: "POST", body });
    const trip = res.trip || res;
    if (res.error) flash(res.error, "error"); else if (okMsg) flash(okMsg, "ok");
    state.current = trip;
    state.trips = await api("/api/trips");
    renderTripList();
    renderCoachTrip(trip);
  });
}

function renderCoachTrip(t) {
  const travelers = t.num_players + t.num_coaches + t.num_staff;
  const head = el("div", { class: "card" },
    el("div", { class: "row between" }, el("h2", {}, `vs ${t.opponent}`), el("span", { class: "pill s-" + t.status }, statusText(t.status))),
    el("p", {}, `${t.venue_name} · ${when(t.match_at)}`),
    el("p", { class: "sub" }, `${travelers} travelers (${t.num_players} players, ${t.num_coaches} coaches, ${t.num_staff} staff) · ${t.nights} night(s) · ${t.meals} team meal(s) · budget ${Number(t.budget) ? money(t.budget) : "none"}`),
    t.diet_tags && t.diet_tags.length ? el("div", { class: "chips" }, "Diet needs:", t.diet_tags.map((d) => el("span", { class: "chip" }, label(d)))) : null,
    el("p", { class: "sub" }, `RSVPs: ${t.rsvp_counts.going} going · ${t.rsvp_counts.maybe} maybe · ${t.rsvp_counts.not_going} not going`));
  const parts = [head];

  if (t.status === "PLANNING") parts.push(planningPanel(t));
  if (t.status === "AWAITING_APPROVAL") parts.push(approvalPanel(t));
  if (t.status === "BOOKED") parts.push(bookedPanel(t));
  parts.push(rosterPanel(t));
  $("#trip-detail").replaceChildren(...parts);
}

function planningPanel(t) {
  const rec = t.ai_recommendation;
  const box = el("div", { class: "stack" });
  if (rec) {
    box.append(el("div", { class: "card ai" },
      el("h3", {}, `Aivana recommends package ${rec.package_no}`), el("p", {}, rec.reason),
      el("p", { class: "sub" }, rec.source === "aivana" ? "Source: Aivana" : (rec.note || "Local ranking"))));
  }
  const replan = el("button", { type: "button", class: "secondary small" }, "Re-price with RSVPs");
  replan.addEventListener("click", () => tripAction(replan, "replan", {}, "Packages re-priced."));
  box.append(el("div", { class: "row between" }, el("h3", {}, `${t.packages.length} packages, cheapest first`), replan));
  if (!t.packages.length) box.append(el("p", { class: "card" }, "No hotel near this venue has enough rooms. Try fewer nights or another venue."));
  box.append(el("div", { class: "packages" }, t.packages.map((p) => packageCard(t, p, rec && rec.package_no === p.package_no))));
  return box;
}

function packageCard(t, p, best) {
  const d = p.detail;
  const choose = el("button", { type: "button" }, best ? "Choose Aivana's pick" : "Choose");
  choose.addEventListener("click", () => tripAction(choose, "select", { package_no: p.package_no }, "Bookings requested. Review and approve them."));
  const h = d.hotel, r = d.restaurant;
  return el("article", { class: "card pkg" + (best ? " best" : "") },
    el("div", { class: "row between" }, el("h3", {}, `Package ${p.package_no}`), el("strong", {}, money(p.total_cost))),
    el("span", { class: "pill " + (d.within_budget ? "ok" : "warn") }, d.within_budget ? "Within budget" : "Over budget"),
    h ? el("p", {}, el("strong", {}, "Hotel: "), `${h.name} ${"★".repeat(h.stars || 0)}`, el("br"),
      el("span", { class: "sub" }, `${h.km_to_venue} km to venue · ${d.rooms} rooms × ${money(h.price_per_night)} · ${money(d.costs.hotel)}`)) : el("p", { class: "sub" }, "Day trip: no hotel"),
    r ? el("p", {}, el("strong", {}, "Meal: "), `${r.name} (${r.cuisine}) `,
      el("button", { type: "button", class: "link", onclick: () => openMenu(r.id, r.name, t.diet_tags) }, "menu"), el("br"),
      el("span", { class: "sub" }, `${money(r.price_per_person)}/person · ${money(d.costs.meal)}`),
      r.missing_diets && r.missing_diets.length ? el("span", { class: "warn-text" }, ` · no main for ${r.missing_diets.map(label).join(", ")}`) : null) : null,
    el("p", {}, el("strong", {}, "Bus: "), `${d.bus.count} × ${d.bus.company} (${d.bus.vehicle}) · ${money(d.costs.bus)}`, el("br"),
      el("span", { class: "sub" }, `${d.route_km} km each way · leaves ${when(d.suggested_departure)}`),
      d.late_arrival ? el("span", { class: "warn-text" }, " · arrives late because of the departure limit") : null),
    choose);
}

function approvalPanel(t) {
  const pending = t.bookings.filter((b) => b.status === "PENDING_APPROVAL");
  const total = pending.reduce((s, b) => s + Number(b.amount), 0);
  const approve = el("button", { type: "button" }, `Approve and book (${money(total)})`);
  approve.addEventListener("click", () => tripAction(approve, "approve", {}, "Booked. Assign rooms, then publish the trip to players."));
  const change = el("button", { type: "button", class: "secondary" }, "Pick a different package");
  change.addEventListener("click", () => tripAction(change, "cancel", {}, "Request cancelled."));
  return el("div", { class: "card stack" },
    el("h3", {}, "Waiting for your approval"),
    el("p", { class: "sub" }, "Nothing is reserved until you approve. Check the cancellation policy."),
    bookingList(pending), el("div", { class: "row" }, approve, change));
}

function bookingList(rows) {
  return el("ul", { class: "list" }, rows.map((b) => el("li", { class: "booking" },
    el("span", { class: "pill s-" + b.status }, b.type), el("span", {}, b.summary),
    el("strong", {}, money(b.amount)), b.confirmation_code ? el("code", {}, b.confirmation_code) : null)));
}

function bookedPanel(t) {
  const confirmed = t.bookings.filter((b) => b.status === "CONFIRMED");
  const rooms = el("button", { type: "button", class: "secondary" }, "Assign rooms and buses");
  rooms.addEventListener("click", () => tripAction(rooms, "rooms", {}, "Rooms and buses assigned (players who RSVP'd going)."));
  const cancel = el("button", { type: "button", class: "danger" }, "Cancel bookings");
  cancel.addEventListener("click", () => { if (window.confirm("Cancel every booking for this trip?")) tripAction(cancel, "cancel", {}, "Bookings cancelled."); });
  const title = el("input", { name: "title", required: true });
  const text = el("textarea", { rows: 7 });
  const draft = el("button", { type: "button", class: "secondary small" }, "Draft with Aivana");
  draft.addEventListener("click", () => busy(draft, async () => {
    const d = await api(`/api/trips/${t.id}/draft`);
    title.value = d.title; text.value = d.body;
  }));
  const publish = el("button", { type: "button" }, t.published ? "Post update to players" : "Publish trip to players");
  publish.addEventListener("click", () => {
    if (!title.value.trim()) return flash("Add a title first.", "error");
    if (t.published) {
      busy(publish, async () => {
        await api(`/api/trips/${t.id}/announcements`, { method: "POST", body: { title: title.value, body: text.value } });
        flash("Announcement posted.", "ok"); title.value = ""; text.value = "";
      });
    } else tripAction(publish, "publish", { title: title.value, body: text.value }, "Published. Players can now see the plan.");
  });
  if (!t.published) setTimeout(() => draft.click(), 0);
  return el("div", { class: "stack" },
    el("div", { class: "card stack" }, el("h3", {}, "Confirmed bookings"), bookingList(confirmed), el("div", { class: "row" }, rooms, cancel)),
    el("div", { class: "card stack" },
      el("div", { class: "row between" }, el("h3", {}, t.published ? "Message players" : "Tell the players"), draft),
      el("label", {}, "Title", title), el("label", {}, "Message", text), publish));
}

function rosterPanel(t) {
  if (!t.participants.length) return el("div", { class: "card sub" }, "No players have joined the team yet. Share the join code.");
  return el("div", { class: "card" }, el("h3", {}, "Players"),
    el("table", { class: "stack-table" },
      el("thead", {}, el("tr", {}, el("th", {}, "Name"), el("th", {}, "RSVP"), el("th", {}, "Room"), el("th", {}, "Bus"))),
      el("tbody", {}, t.participants.map((p) => el("tr", {},
        el("td", { "data-l": "Name" }, p.full_name), el("td", { "data-l": "RSVP" }, label(p.rsvp)),
        el("td", { "data-l": "Room" }, p.room_no ?? "–"), el("td", { "data-l": "Bus" }, p.bus_no ?? "–"))))));
}

/* ---------- menu dialog ---------- */
async function openMenu(rid, name, preselect = []) {
  const dlg = $("#menu-dialog");
  $("#menu-title").textContent = name;
  const active = new Set(preselect.slice(0, 1));
  const render = async () => {
    $("#menu-filters").replaceChildren(...CFG.dietTags.map((tg) => el("button", {
      type: "button", class: "chip" + (active.has(tg) ? " on" : ""), "aria-pressed": active.has(tg) ? "true" : "false",
      onclick: () => { active.has(tg) ? active.delete(tg) : active.add(tg); render(); } }, label(tg))));
    const items = await api(`/api/restaurants/${rid}/menu?diet=${[...active].join(",")}`);
    $("#menu-items").replaceChildren(...(items.length ? items.map((m) => el("li", { class: "row between" },
      el("span", {}, m.name, el("span", { class: "sub" }, ` · ${m.course} · ${(m.diet_tags || []).map(label).join(", ")}`)),
      el("strong", {}, money(m.price)))) : [el("li", { class: "sub" }, "Nothing on this menu matches every selected need.")]));
  };
  await render();
  dlg.showModal();
}

/* ================= PLAYER ================= */
async function playerHome() {
  show("player");
  $("#p-team").textContent = state.me.team ? state.me.team.name : "";
  dietBoxes($("#my-diet"), state.me.profile.diet_tags || []);
  const [trips, feed] = await Promise.all([api("/api/trips"), api("/api/announcements")]);
  renderPlayerTrips(trips);
  renderFeed(feed);
}

$("#save-diet").addEventListener("click", (e) => busy(e.target, async () => {
  await api("/api/me", { method: "PATCH", body: { diet_tags: checkedDiets($("#my-diet")) } });
  state.me = await api("/api/me");
  flash("Saved. Menus now show dishes that fit.", "ok");
  playerHome();
}));

function renderPlayerTrips(trips) {
  const box = $("#player-trips");
  if (!trips.length) return box.replaceChildren(el("p", { class: "card sub" }, "No trips yet."));
  box.replaceChildren(...trips.map((t) => {
    const my = t.my || {};
    const rsvp = el("div", { class: "row" }, ["going", "maybe", "not_going"].map((s) => {
      const b = el("button", { type: "button", class: "small " + (my.rsvp === s ? "" : "secondary"), "aria-pressed": my.rsvp === s ? "true" : "false" }, label(s));
      b.addEventListener("click", () => busy(b, async () => { await api(`/api/trips/${t.id}/rsvp`, { method: "POST", body: { rsvp: s } }); playerHome(); }));
      return b;
    }));
    const card = el("article", { class: "card stack" },
      el("div", { class: "row between" }, el("h3", {}, `vs ${t.opponent}`), el("span", { class: "pill s-" + t.status }, t.published ? "Plan ready" : "Being planned")),
      el("p", {}, `${t.venue_name} · ${when(t.match_at)}`),
      el("div", {}, el("span", { class: "sub" }, "Are you going? "), rsvp));
    if (t.bus) {                                   // only present once the coach publishes the plan
      card.append(el("p", {}, el("strong", {}, "Bus leaves: "), when(t.suggested_departure), ` · ${t.bus.company}`,
        my.bus_no ? ` · bus ${my.bus_no}` : ""));
      if (t.hotel) card.append(el("p", {}, el("strong", {}, "Hotel: "), t.hotel.name, my.room_no ? ` · room ${my.room_no}` : "",
        el("br"), el("span", { class: "sub" }, `${t.hotel.address || ""} ${t.hotel.km_to_venue} km from the stadium · `),
        el("a", { href: mapLink(t.hotel.lat, t.hotel.lng), target: "_blank", rel: "noopener" }, "map")));
      if (t.restaurant) {
        card.append(el("p", {}, el("strong", {}, "Team meal: "), `${t.restaurant.name} (${t.restaurant.cuisine}) · `,
          el("a", { href: mapLink(t.restaurant.lat, t.restaurant.lng), target: "_blank", rel: "noopener" }, "map")));
        const diet = state.me.profile.diet_tags || [];
        card.append(el("details", {}, el("summary", {}, diet.length ? `Dishes that fit your diet (${diet.map(label).join(", ")})` : "Menu"),
          el("ul", { class: "list" }, t.my_menu.length ? t.my_menu.map((m) => el("li", {}, `${m.name} · ${m.course}`))
            : [el("li", { class: "sub" }, "No dish matches all your needs. Tell your coach.")])));
      }
    }
    return card;
  }));
}

function renderFeed(feed) {
  const box = $("#feed");
  if (!feed.length) return box.replaceChildren(el("p", { class: "card sub" }, "No announcements yet."));
  box.replaceChildren(...feed.map((a) => {
    const save = el("button", { type: "button", class: "link" }, a.saved ? "Saved ✓" : "Save");
    save.addEventListener("click", () => busy(save, async () => {
      const r = await api(`/api/announcements/${a.id}/save`, { method: "POST" });
      save.textContent = r.saved ? "Saved ✓" : "Save";
    }));
    const card = el("article", { class: "card" + (a.read ? "" : " unread") },
      el("div", { class: "row between" }, el("h3", {}, a.title), save),
      el("p", { class: "sub" }, `${a.author} · ${when(a.created_at)}`),
      el("p", { class: "pre" }, a.body));
    if (!a.read) api(`/api/announcements/${a.id}/read`, { method: "POST" }).catch(() => {});
    return card;
  }));
}

/* ---------- start ---------- */
setupAuth();
setupOnboard();
if (sb) sb.auth.onAuthStateChange((event) => { if (event === "SIGNED_OUT") show("auth"); });
boot();
