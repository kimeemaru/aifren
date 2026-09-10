using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

namespace AIFren.UnityPoc.UI
{
    // Presentation only: no command/store/subtitle or avatar ownership.
    internal sealed class SceneDrawerPresenter : MonoBehaviour
    {
        internal const float ExitGrace = .30f;
        private Button tab;
        private RectTransform panel;
        private CanvasGroup group;
        private bool allowed, pinned, overTab, overPanel;
        private float progress, closeAfter;
        internal bool Expanded => progress > .99f;
        internal bool Pinned => pinned;
        internal bool HasKeyboardFocus => allowed && OwnsFocus();

        internal void Initialize(Button affordance, RectTransform drawer)
        {
            tab = affordance; panel = drawer;
            group = panel.gameObject.AddComponent<CanvasGroup>();
            tab.onClick.AddListener(Toggle);
            var tabHover = tab.gameObject.AddComponent<SceneDrawerPointer>();
            tabHover.Changed = over => Pointer(true, over, Time.unscaledTime);
            tabHover.Cancelled = CloseImmediately;
            var panelHover = panel.gameObject.AddComponent<SceneDrawerPointer>();
            panelHover.Changed = over => Pointer(false, over, Time.unscaledTime);
            panelHover.Cancelled = CloseImmediately;
            SetAvailable(false);
        }
        internal void SetAvailable(bool value)
        {
            if (allowed == value && tab != null && tab.gameObject.activeSelf == value) return;
            allowed = value; tab.gameObject.SetActive(value);
            if (!value) CloseImmediately();
        }
        internal void Pointer(bool affordance, bool inside, float now)
        {
            if (affordance) overTab = inside; else overPanel = inside;
            if (inside) closeAfter = now + ExitGrace;
            else closeAfter = Mathf.Max(closeAfter, now + ExitGrace);
        }
        internal void Toggle()
        {
            pinned = !pinned;
            if (!pinned) ReleaseFocus();
        }
        internal void CloseImmediately()
        {
            pinned = overTab = overPanel = false; progress = closeAfter = 0;
            ReleaseFocus(); Apply();
        }
        private bool OwnsFocus()
        {
            var selected = EventSystem.current != null ? EventSystem.current.currentSelectedGameObject : null;
            return selected != null && (selected == tab.gameObject || selected.transform.IsChildOf(panel));
        }
        private void ReleaseFocus()
        {
            if (OwnsFocus()) EventSystem.current.SetSelectedGameObject(null);
        }
        private void Update()
        {
            if (panel == null || !allowed) return;
            if (Input.GetKeyDown(KeyCode.Escape) && (Expanded || pinned))
            { CloseImmediately(); return; }
            Tick(Time.unscaledTime, Time.unscaledDeltaTime, OwnsFocus());
        }
        internal void Tick(float now, float delta, bool focus)
        {
            if (overTab || overPanel || focus || pinned) closeAfter = now + ExitGrace;
            bool open = allowed && (overTab || overPanel || focus || pinned || now < closeAfter);
            float next = Mathf.MoveTowards(progress, open ? 1 : 0, delta / .16f);
            if (next == progress) return;
            progress = next; Apply();
        }
        private void Apply()
        {
            if (panel == null) return;
            panel.gameObject.SetActive(allowed && progress > 0);
            panel.anchoredPosition = new Vector2(Mathf.Lerp(-panel.rect.width - 8, 10, Mathf.SmoothStep(0, 1, progress)), 0);
            group.alpha = progress;
            group.interactable = group.blocksRaycasts = allowed && progress > .90f;
        }
    }
    internal sealed class SceneDrawerPointer : MonoBehaviour, IPointerEnterHandler, IPointerExitHandler, ICancelHandler
    {
        internal System.Action<bool> Changed;
        internal System.Action Cancelled;
        public void OnPointerEnter(PointerEventData data) => Changed?.Invoke(true);
        public void OnPointerExit(PointerEventData data) => Changed?.Invoke(false);
        public void OnCancel(BaseEventData data) { Cancelled?.Invoke(); data.Use(); }
    }
}
