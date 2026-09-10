#if UNITY_EDITOR || DEVELOPMENT_BUILD
using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;

namespace AIFren.UnityPoc.UI
{
    // Explicit finite-plan input only. Uses the actual canvas raycast and native
    // pointer/focus dispatch; never moves the desktop pointer or polls global input.
    internal sealed class NativeQaPointer : PointerInputModule
    {
        private PointerEventData pointer;
        private readonly List<RaycastResult> hits = new List<RaycastResult>();
        public override void Process() { }
        public override bool ShouldActivateModule() => false;

        internal GameObject Move(Vector2 position)
        {
            if (!PresentationPreferences.IsIsolated || (!NativeQaSession.Active && !Application.isEditor))
                throw new InvalidOperationException("Finite isolated input is required.");
            if (pointer == null) pointer = new PointerEventData(eventSystem) { pointerId = -101 };
            pointer.position = position;
            hits.Clear(); eventSystem.RaycastAll(pointer, hits);
            pointer.pointerCurrentRaycast = FindFirstRaycast(hits);
            var target = pointer.pointerCurrentRaycast.gameObject;
            HandlePointerExitAndEnter(pointer, target);
            return target;
        }

        internal void Target(RectTransform expected, bool click = false)
        {
            Canvas.ForceUpdateCanvases();
            var canvas = expected.GetComponentInParent<Canvas>();
            var camera = canvas.renderMode == RenderMode.ScreenSpaceOverlay ? null : canvas.worldCamera;
            var position = RectTransformUtility.WorldToScreenPoint(camera, expected.TransformPoint(expected.rect.center));
            var hit = Move(position);
            if (hit == null || (hit.transform != expected && !hit.transform.IsChildOf(expected)))
                throw new InvalidOperationException("The requested finite UI target is not reachable.");
            if (!click) return;
            DeselectIfSelectionChanged(hit, pointer);
            pointer.button = PointerEventData.InputButton.Left;
            pointer.pressPosition = pointer.position;
            pointer.pointerPressRaycast = pointer.pointerCurrentRaycast;
            pointer.eligibleForClick = true;
            var pressed = ExecuteEvents.ExecuteHierarchy(hit, pointer, ExecuteEvents.pointerDownHandler)
                ?? ExecuteEvents.GetEventHandler<IPointerClickHandler>(hit);
            pointer.pointerPress = pressed;
            ExecuteEvents.Execute(pressed, pointer, ExecuteEvents.pointerUpHandler);
            if (pressed == ExecuteEvents.GetEventHandler<IPointerClickHandler>(hit))
                ExecuteEvents.Execute(pressed, pointer, ExecuteEvents.pointerClickHandler);
            pointer.eligibleForClick = false; pointer.pointerPress = null;
        }

        internal void SubmitSelected(bool cancel)
        {
            var selected = eventSystem.currentSelectedGameObject;
            if (selected == null) throw new InvalidOperationException("No finite UI selection.");
            var data = new BaseEventData(eventSystem);
            if (cancel) ExecuteEvents.ExecuteHierarchy(selected, data, ExecuteEvents.cancelHandler);
            else ExecuteEvents.Execute(selected, data, ExecuteEvents.submitHandler);
        }
    }
}
#endif
