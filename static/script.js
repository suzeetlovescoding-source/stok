(() => {
  "use strict";

  const form = document.getElementById("fetch-form");
  const urlInput = document.getElementById("url-input");
  const fetchBtn = document.getElementById("fetch-btn");
  const formError = document.getElementById("form-error");

  const result = document.getElementById("result");
  const thumb = document.getElementById("thumb");
  const durationBadge = document.getElementById("duration-badge");
  const authorAvatar = document.getElementById("author-avatar");
  const authorNickname = document.getElementById("author-nickname");
  const authorUsername = document.getElementById("author-username");
  const vDesc = document.getElementById("v-desc");
  const statsRow = document.getElementById("stats-row");
  const downloadsGrid = document.getElementById("downloads-grid");
  const imagesWrap = document.getElementById("images-wrap");
  const imagesGrid = document.getElementById("images-grid");
  const imagesCount = document.getElementById("images-count");

  const dlProgress = document.getElementById("dl-progress");
  const dlStatus = document.getElementById("dl-status");
  const dlPercent = document.getElementById("dl-percent");
  const dlFill = document.getElementById("dl-fill");

  const toastEl = document.getElementById("toast");

  let toastTimer = null;

  function toast(message, isError = false) {
    toastEl.textContent = message;
    toastEl.classList.toggle("error", isError);
    toastEl.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 4000);
  }

  function setFormError(message) {
    if (!message) {
      formError.hidden = true;
      formError.textContent = "";
      return;
    }
    formError.hidden = false;
    formError.textContent = message;
  }

  function setLoading(isLoading) {
    fetchBtn.disabled = isLoading;
    fetchBtn.classList.toggle("loading", isLoading);
  }

  function formatDuration(ms) {
    if (!ms) return "";
    const totalSeconds = Math.round(ms / 1000);
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function formatCount(n) {
    if (n === null || n === undefined) return null;
    if (n >= 1_000_000_000) return (n / 1_000_000_000).toFixed(1).replace(/\.0$/, "") + "B";
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  }

  async function postJSON(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data;
    try {
      data = await res.json();
    } catch {
      throw new Error("Unexpected response from server.");
    }
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || "Something went wrong.");
    }
    return data;
  }

  function addStat(label, value) {
    if (value === null || value === undefined) return;
    const span = document.createElement("span");
    span.className = "stat";
    span.innerHTML = `<b>${value}</b> ${label}`;
    statsRow.appendChild(span);
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    setFormError(null);
    const url = urlInput.value.trim();
    if (!url) return;

    setLoading(true);
    result.hidden = true;

    try {
      const info = await postJSON("/api/info", { url });

      thumb.src = info.thumbnail || "";
      thumb.alt = info.desc || "TikTok media";
      durationBadge.textContent = formatDuration(info.durationMs);
      durationBadge.hidden = !info.durationMs;

      authorAvatar.hidden = !info.author?.avatar;
      authorAvatar.src = info.author?.avatar || "";
      authorNickname.textContent = info.author?.nickname || "Unknown creator";
      authorUsername.textContent = info.author?.username ? `@${info.author.username}` : "";

      vDesc.textContent = info.desc || "";
      vDesc.hidden = !info.desc;

      statsRow.innerHTML = "";
      const plays = formatCount(info.stats?.plays);
      const likes = formatCount(info.stats?.likes);
      const comments = formatCount(info.stats?.comments);
      const shares = formatCount(info.stats?.shares);
      if (plays) addStat("plays", plays);
      if (likes) addStat("likes", likes);
      if (comments) addStat("comments", comments);
      if (shares) addStat("shares", shares);

      downloadsGrid.innerHTML = "";
      const entries = Object.entries(info.downloads || {});
      if (entries.length === 0) {
        downloadsGrid.innerHTML = `<p style="color:var(--ink-faint); font-size:0.88rem;">No direct downloads found for this post.</p>`;
      } else {
        entries.forEach(([key, item]) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "fmt-btn";
          btn.innerHTML = `<span class="fmt-label">${item.label}</span><span class="fmt-sub">${item.ext.toUpperCase()}</span>`;
          btn.addEventListener("click", () => {
            document.querySelectorAll(".fmt-btn.selected, .image-tile.selected").forEach((b) => b.classList.remove("selected"));
            btn.classList.add("selected");
            const filename = `stok_${info.id || "media"}_${key}.${item.ext}`;
            startDownload(item.url, filename, `${item.label}`);
          });
          downloadsGrid.appendChild(btn);
        });
      }

      imagesGrid.innerHTML = "";
      if (info.images && info.images.length > 0) {
        imagesWrap.hidden = false;
        imagesCount.textContent = `${info.images.length} item${info.images.length === 1 ? "" : "s"}`;
        info.images.forEach((img, idx) => {
          const tile = document.createElement("div");
          tile.className = "image-tile";
          const preview = img.thumb || img.url;
          const badge = img.isVideo ? `<span class="tile-play" aria-hidden="true">▶</span>` : "";
          tile.innerHTML = `<span class="tile-num">${idx + 1}</span>${badge}<img src="${preview}" alt="Item ${idx + 1}" loading="lazy">`;
          tile.addEventListener("click", () => {
            document.querySelectorAll(".fmt-btn.selected, .image-tile.selected").forEach((b) => b.classList.remove("selected"));
            tile.classList.add("selected");
            const ext = img.ext || "jpeg";
            const filename = `stok_${info.id || "media"}_item${idx + 1}.${ext}`;
            startDownload(img.url, filename, `Item ${idx + 1}`);
          });
          imagesGrid.appendChild(tile);
        });
      } else {
        imagesWrap.hidden = true;
      }

      dlProgress.hidden = true;
      result.hidden = false;
      result.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) {
      setFormError(err.message);
    } finally {
      setLoading(false);
    }
  });

  async function startDownload(src, filename, label) {
    dlProgress.hidden = false;
    dlFill.style.width = "0%";
    dlPercent.textContent = "0%";
    dlStatus.textContent = `Preparing ${label}…`;

    try {
      const params = new URLSearchParams({ src, filename });
      const res = await fetch(`/api/download?${params.toString()}`);

      if (!res.ok) {
        let message = "Download failed.";
        try {
          const data = await res.json();
          message = data.error || message;
        } catch {
          /* ignore parse errors */
        }
        throw new Error(message);
      }

      const contentLength = res.headers.get("Content-Length");
      const total = contentLength ? parseInt(contentLength, 10) : 0;
      let received = 0;

      if (!res.body || !res.body.getReader) {
        const blob = await res.blob();
        triggerBlobDownload(blob, filename);
        dlStatus.textContent = "Download complete";
        dlPercent.textContent = "100%";
        dlFill.style.width = "100%";
        toast(`Saved "${filename}"`);
        return;
      }

      const reader = res.body.getReader();
      const chunks = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.length;

        if (total) {
          const pct = Math.min(100, Math.round((received / total) * 100));
          dlFill.style.width = pct + "%";
          dlPercent.textContent = pct + "%";
          dlStatus.textContent = `Downloading… ${(received / 1_048_576).toFixed(1)} / ${(total / 1_048_576).toFixed(1)} MB`;
        } else {
          dlStatus.textContent = `Downloading… ${(received / 1_048_576).toFixed(1)} MB`;
        }
      }

      const blob = new Blob(chunks);
      triggerBlobDownload(blob, filename);

      dlFill.style.width = "100%";
      dlPercent.textContent = "100%";
      dlStatus.textContent = "Download complete";
      toast(`Saved "${filename}"`);
    } catch (err) {
      dlStatus.textContent = "Download failed";
      toast(err.message || "Download failed.", true);
    }
  }

  function triggerBlobDownload(blob, filename) {
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 4000);
  }
})();
