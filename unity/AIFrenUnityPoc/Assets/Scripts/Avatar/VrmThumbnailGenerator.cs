using System;
using System.IO;
using System.Threading.Tasks;
using UniVRM10;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    internal static class VrmThumbnailGenerator
    {
        internal const int ThumbnailSize = 256;
        // Bump when the standardized framing changes so stale cached previews
        // are replaced once rather than surviving indefinitely.
        internal const int ThumbnailVersion = 3;
        // Keep preview renderers far outside the live viewer's stable camera volume.
        // This avoids a second imported avatar ever becoming part of the companion view.
        private static readonly Vector3 PreviewOrigin = new Vector3(10000f, 0f, 0f);

        internal static async Task<bool> TryGenerateAsync(string modelPath, string thumbnailPath)
        {
            if (HasValidThumbnail(thumbnailPath)) return true;
            GameObject avatar = null; GameObject cameraObject = null; GameObject lightObject = null; RenderTexture target = null; Texture2D output = null;
            RenderTexture previousActive = null;
            bool changedActiveTarget = false;
            try
            {
                Vrm10Instance instance = await Vrm10.LoadPathAsync(modelPath, canLoadVrm0X: true, showMeshes: true);
                avatar = instance.gameObject;
                avatar.transform.position = PreviewOrigin;
                Bounds bounds = new Bounds(); bool found = false;
                foreach (Renderer renderer in avatar.GetComponentsInChildren<Renderer>()) { if (!found) { bounds=renderer.bounds; found=true; } else bounds.Encapsulate(renderer.bounds); }
                if (!found) throw new InvalidOperationException("VRM has no renderers.");
                cameraObject = new GameObject("AIFren Thumbnail Camera"); Camera camera = cameraObject.AddComponent<Camera>();
                camera.enabled = false; camera.clearFlags=CameraClearFlags.SolidColor; camera.backgroundColor=new Color(.96f,.96f,.94f,1f); camera.fieldOfView=30f; camera.aspect = 1f;
                GetPortraitFrame(avatar, bounds, out Vector3 center, out float halfHeight, out float halfWidth);
                float verticalDistance = halfHeight / Mathf.Tan(camera.fieldOfView * Mathf.Deg2Rad * .5f);
                float horizontalDistance = halfWidth / Mathf.Tan(camera.fieldOfView * Mathf.Deg2Rad * .5f);
                float distance = Mathf.Max(verticalDistance, horizontalDistance) * 1.12f;
                Vector3 facing = GetFacingDirection(avatar);
                // VRM import orientation is not reliably world-Z. Place the
                // preview camera on the humanoid face side instead of assuming
                // a fixed axis, then look back through the face toward center.
                camera.transform.position = center + facing * distance;
                camera.transform.LookAt(center, Vector3.up);
                lightObject=new GameObject("AIFren Thumbnail Light"); Light light=lightObject.AddComponent<Light>(); light.type=LightType.Directional; light.intensity=.9f; light.transform.rotation=Quaternion.Euler(38f,-28f,0f);
                target=new RenderTexture(ThumbnailSize,ThumbnailSize,24,RenderTextureFormat.ARGB32); camera.targetTexture=target; camera.Render();
                previousActive=RenderTexture.active; changedActiveTarget=true; RenderTexture.active=target; output=new Texture2D(ThumbnailSize,ThumbnailSize,TextureFormat.RGBA32,false); output.ReadPixels(new Rect(0,0,ThumbnailSize,ThumbnailSize),0,0); output.Apply(); RenderTexture.active=previousActive; changedActiveTarget=false;
                Directory.CreateDirectory(Path.GetDirectoryName(thumbnailPath)); File.WriteAllBytes(thumbnailPath,output.EncodeToPNG());
                File.WriteAllText(VersionMarkerPath(thumbnailPath), ThumbnailVersion.ToString());
                return true;
            }
            catch (Exception error) { Debug.LogWarning("AIFren model thumbnail generation failed: "+error.Message); return false; }
            finally { if(changedActiveTarget)RenderTexture.active=previousActive; if(output!=null) UnityEngine.Object.Destroy(output); if(target!=null){target.Release();UnityEngine.Object.Destroy(target);} if(lightObject!=null)UnityEngine.Object.Destroy(lightObject); if(cameraObject!=null)UnityEngine.Object.Destroy(cameraObject); if(avatar!=null)UnityEngine.Object.Destroy(avatar); }
        }

        private static bool HasValidThumbnail(string thumbnailPath)
        {
            if (string.IsNullOrWhiteSpace(thumbnailPath) || !File.Exists(thumbnailPath)) return false;
            Texture2D probe = null;
            try
            {
                byte[] bytes = File.ReadAllBytes(thumbnailPath);
                probe = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                return bytes.Length > 0 && ImageConversion.LoadImage(probe, bytes, true) && probe.width == ThumbnailSize && probe.height == ThumbnailSize &&
                    File.Exists(VersionMarkerPath(thumbnailPath)) && File.ReadAllText(VersionMarkerPath(thumbnailPath)).Trim() == ThumbnailVersion.ToString();
            }
            catch { return false; }
            finally { if (probe != null) UnityEngine.Object.Destroy(probe); }
        }

        internal static string VersionMarkerPath(string thumbnailPath) => thumbnailPath + ".version";
        internal static bool NeedsGeneration(string thumbnailPath) => !HasValidThumbnail(thumbnailPath);

        private static void GetPortraitFrame(GameObject avatar, Bounds fullBounds, out Vector3 center, out float halfHeight, out float halfWidth)
        {
            float fullHeight = Mathf.Max(fullBounds.size.y, .1f);
            Animator animator = avatar.GetComponentInChildren<Animator>();
            Transform head = animator != null && animator.isHuman ? animator.GetBoneTransform(HumanBodyBones.Head) : null;
            if (head == null)
            {
                // Proportion-based fallback: the upper third of the renderer
                // envelope reads as a head-and-shoulders card for non-humanoid
                // VRMs without trying to chase animated bounds.
                center = fullBounds.center + Vector3.up * fullHeight * .22f;
                halfHeight = Mathf.Max(fullHeight * .30f, .42f);
                halfWidth = Mathf.Max(fullBounds.extents.x * .62f, halfHeight * .72f);
                return;
            }

            float top = Mathf.Min(fullBounds.max.y, head.position.y + fullHeight * .18f);
            float bottom = Mathf.Max(fullBounds.min.y, head.position.y - fullHeight * .46f);
            center = new Vector3(head.position.x, (top + bottom) * .5f, head.position.z);
            halfHeight = Mathf.Max((top - bottom) * .5f, .38f);

            // Only use geometry intersecting the portrait band for width so a
            // skirt, cape, or wide lower-body clothing cannot shrink the face.
            float minX = float.PositiveInfinity, maxX = float.NegativeInfinity;
            foreach (Renderer renderer in avatar.GetComponentsInChildren<Renderer>())
            {
                Bounds current = renderer.bounds;
                if (current.max.y < bottom || current.min.y > top) continue;
                minX = Mathf.Min(minX, current.min.x); maxX = Mathf.Max(maxX, current.max.x);
            }
            halfWidth = minX <= maxX ? Mathf.Max((maxX - minX) * .5f, halfHeight * .62f) : halfHeight * .78f;
        }

        private static Vector3 GetFacingDirection(GameObject avatar)
        {
            Animator animator = avatar.GetComponentInChildren<Animator>();
            Transform head = animator != null && animator.isHuman ? animator.GetBoneTransform(HumanBodyBones.Head) : null;
            Vector3 facing = head != null ? Vector3.ProjectOnPlane(head.forward, Vector3.up) : Vector3.zero;
            if (facing.sqrMagnitude < .0001f)
                facing = Vector3.ProjectOnPlane(avatar.transform.forward, Vector3.up);
            return facing.sqrMagnitude < .0001f ? Vector3.forward : facing.normalized;
        }
    }
}
