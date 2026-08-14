using System;
using UnityEngine;
using UnityEngine.EventSystems;

namespace AIFren.UnityPoc.UI
{
    /// <summary>
    /// Small pointer adapter for the avatar RawImage. It contains no framing
    /// state: the presentation controller decides when a framing session is
    /// active and applies the resulting deltas through AvatarPresentationFramingState.
    /// </summary>
    public sealed class AvatarFramingInputSurface : MonoBehaviour, IBeginDragHandler, IDragHandler, IScrollHandler
    {
        public event Action<Vector2> Dragged;
        public event Action<float> Scrolled;

        public void OnBeginDrag(PointerEventData eventData)
        {
            // IDragHandler supplies deltas; no separate capture state is needed.
        }

        public void OnDrag(PointerEventData eventData)
        {
            Dragged?.Invoke(eventData.delta);
        }

        public void OnScroll(PointerEventData eventData)
        {
            if (Mathf.Abs(eventData.scrollDelta.y) > .001f)
            {
                Scrolled?.Invoke(eventData.scrollDelta.y);
            }
        }
    }
}
