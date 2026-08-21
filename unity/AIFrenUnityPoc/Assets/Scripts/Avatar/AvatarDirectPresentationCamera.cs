using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    public struct AvatarDirectCameraView
    {
        public float fieldOfView;
        public Vector2 lensShift;
    }

    /// <summary>Maps presentation-side Avatar View values onto a direct camera.</summary>
    public static class AvatarDirectPresentationCamera
    {
        public static AvatarDirectCameraView FromPresentation(float baseFieldOfView, AvatarPresentationValues values)
        {
            float scale = Mathf.Max(1f, values.scale);
            float baseHalfRadians = Mathf.Deg2Rad * Mathf.Clamp(baseFieldOfView, 1f, 179f) * .5f;
            float directFieldOfView = Mathf.Rad2Deg * 2f * Mathf.Atan(Mathf.Tan(baseHalfRadians) / scale);
            return new AvatarDirectCameraView
            {
                fieldOfView = directFieldOfView,
                // RawImage translation moves the rendered avatar right/up for
                // positive X/Y. A camera lens shifts in the opposite direction
                // to place that same source image motion on screen.
                lensShift = new Vector2(-values.x, -values.y)
            };
        }
    }
}
