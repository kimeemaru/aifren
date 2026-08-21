using System;
using UnityEngine;
using UnityEngine.EventSystems;

namespace AIFren.UnityPoc.UI
{
    public sealed class AvatarPresentationInputSurface : MonoBehaviour, IDragHandler, IScrollHandler
    {
        public event Action<Vector2> Dragged;
        public event Action<float> Scrolled;
        public void OnDrag(PointerEventData eventData) => Dragged?.Invoke(eventData.delta);
        public void OnScroll(PointerEventData eventData)
        {
            if (Mathf.Abs(eventData.scrollDelta.y) > .001f) Scrolled?.Invoke(eventData.scrollDelta.y);
        }
    }
}
