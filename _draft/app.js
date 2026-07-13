const siteConfig = {
  watermarkText: "EndofTimeWorks",
};

const photos = Array.isArray(window.photoLibrary) ? window.photoLibrary : [];
const state = {
  filter: "all",
  search: "",
  sort: "newest",
  viewerPhotos: [],
  viewerIndex: 0,
};

const gallery = document.querySelector("#gallery");
const filters = document.querySelector("#filters");
const searchInput = document.querySelector("#search");
const sortInput = document.querySelector("#sort");
const viewer = document.querySelector("#viewer");
const viewerImage = document.querySelector("#viewer-image");
const viewerTitle = document.querySelector("#viewer-title");
const viewerCategory = document.querySelector("#viewer-category");
const viewerDescription = document.querySelector("#viewer-description");
const viewerCounter = document.querySelector("#viewer-counter");
const viewerWatermark = document.querySelector(".viewer__watermark");
const viewerClose = document.querySelector(".viewer__close");
const viewerPrevious = document.querySelector(".viewer__nav--previous");
const viewerNext = document.querySelector(".viewer__nav--next");

function escapeHtml(value) {
  const element = document.createElement("div");
  element.textContent = String(value ?? "");
  return element.innerHTML;
}

function normalizePhoto(photo, index) {
  return {
    id: photo.id || `photo-${index + 1}`,
    src: photo.src || "",
    alt: photo.alt || photo.title || "Photography portfolio image",
    title: photo.title || `Untitled ${index + 1}`,
    category: String(photo.category || "uncategorized").toLowerCase(),
    date: photo.date || "",
    location: photo.location || "",
    description: photo.description || "",
    tags: Array.isArray(photo.tags) ? photo.tags : [],
  };
}

const normalizedPhotos = photos.map(normalizePhoto).filter((photo) => photo.src);

function renderFilters() {
  const categories = [...new Set(normalizedPhotos.map((photo) => photo.category))]
    .sort((a, b) => a.localeCompare(b));

  categories.forEach((category) => {
    const button = document.createElement("button");
    button.className = "tab";
    button.type = "button";
    button.dataset.filter = category;
    button.textContent = category;
    filters.append(button);
  });
}

function getVisiblePhotos() {
  const query = state.search.trim().toLowerCase();

  return normalizedPhotos
    .filter((photo) => state.filter === "all" || photo.category === state.filter)
    .filter((photo) => {
      if (!query) {
        return true;
      }

      return [
        photo.title,
        photo.category,
        photo.location,
        photo.description,
        ...photo.tags,
      ]
        .join(" ")
        .toLowerCase()
        .includes(query);
    })
    .sort((a, b) => {
      if (state.sort === "title") {
        return a.title.localeCompare(b.title);
      }

      const direction = state.sort === "oldest" ? 1 : -1;
      return direction * a.date.localeCompare(b.date);
    });
}

function renderGallery() {
  const visiblePhotos = getVisiblePhotos();

  if (!normalizedPhotos.length) {
    gallery.innerHTML = `
      <div class="empty">
        <strong>Gallery coming soon</strong>
        <span>Photos will appear here after they are edited and prepared.</span>
      </div>
    `;
    return;
  }

  if (!visiblePhotos.length) {
    gallery.innerHTML = '<div class="empty">No photos match this view.</div>';
    return;
  }

  gallery.innerHTML = visiblePhotos
    .map((photo, index) => {
      const details = [photo.location, photo.date].filter(Boolean).join(" - ");

      return `
        <article class="photo-card">
          <button class="photo-button" type="button" data-index="${index}">
            <div class="photo-frame">
              <img
                class="photo-image"
                src="${escapeHtml(photo.src)}"
                alt="${escapeHtml(photo.alt)}"
                loading="lazy"
                decoding="async"
                draggable="false"
              >
              <span class="watermark" aria-hidden="true">${siteConfig.watermarkText}</span>
              <span class="photo-tag">${escapeHtml(photo.category)}</span>
            </div>
            <div class="photo-meta">
              <h2>${escapeHtml(photo.title)}</h2>
              ${details ? `<p>${escapeHtml(details)}</p>` : ""}
            </div>
          </button>
        </article>
      `;
    })
    .join("");

  gallery.querySelectorAll(".photo-button").forEach((button) => {
    button.addEventListener("click", () => {
      openViewer(visiblePhotos, Number(button.dataset.index));
    });
  });

  gallery.querySelectorAll(".photo-image").forEach((image) => {
    image.addEventListener("error", () => {
      image.closest(".photo-card").classList.add("has-error");
      image.alt = "Photo unavailable";
    });
  });
}

function showViewerPhoto() {
  const photo = state.viewerPhotos[state.viewerIndex];
  if (!photo) {
    return;
  }

  viewerImage.src = photo.src;
  viewerImage.alt = photo.alt;
  viewerWatermark.textContent = siteConfig.watermarkText;
  viewerCategory.textContent = photo.category;
  viewerTitle.textContent = photo.title;
  viewerDescription.textContent = photo.description;
  viewerCounter.textContent = `${state.viewerIndex + 1} / ${state.viewerPhotos.length}`;
  viewerPrevious.disabled = state.viewerPhotos.length < 2;
  viewerNext.disabled = state.viewerPhotos.length < 2;
}

function openViewer(visiblePhotos, index) {
  state.viewerPhotos = visiblePhotos;
  state.viewerIndex = index;
  showViewerPhoto();
  viewer.showModal();
}

function moveViewer(direction) {
  const count = state.viewerPhotos.length;
  if (count < 2) {
    return;
  }

  state.viewerIndex = (state.viewerIndex + direction + count) % count;
  showViewerPhoto();
}

filters.addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (!tab) {
    return;
  }

  state.filter = tab.dataset.filter;
  filters.querySelectorAll(".tab").forEach((item) => {
    item.classList.toggle("is-active", item === tab);
  });
  renderGallery();
});

searchInput.addEventListener("input", () => {
  state.search = searchInput.value;
  renderGallery();
});

sortInput.addEventListener("change", () => {
  state.sort = sortInput.value;
  renderGallery();
});

viewerClose.addEventListener("click", () => viewer.close());
viewerPrevious.addEventListener("click", () => moveViewer(-1));
viewerNext.addEventListener("click", () => moveViewer(1));

document.addEventListener("keydown", (event) => {
  if (!viewer.open) {
    return;
  }

  if (event.key === "ArrowLeft") {
    moveViewer(-1);
  } else if (event.key === "ArrowRight") {
    moveViewer(1);
  }
});

viewer.addEventListener("click", (event) => {
  if (event.target === viewer) {
    viewer.close();
  }
});

viewer.addEventListener("close", () => {
  viewerImage.removeAttribute("src");
  state.viewerPhotos = [];
  state.viewerIndex = 0;
});

renderFilters();
renderGallery();
