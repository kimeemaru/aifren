using System;
using UniVRM10;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    // Structural corroboration for finite native QA. A reviewed framebuffer
    // remains required; renderer visibility alone cannot prove final pixels.
    public static class AvatarVisualProbe
    {
        [Serializable] internal sealed class Result
        {
            public bool ready, humanoid, vrm, directCamera;
            public int renderers, visibleRenderers, vertices;
            public string code, model;
            public Vector3 boundsCenter, boundsSize, cameraPosition;
            public float fieldOfView;
        }

        public static bool HasGeometry(GameObject avatar)
        {
            if (avatar == null) return false;
            foreach (SkinnedMeshRenderer renderer in avatar.GetComponentsInChildren<SkinnedMeshRenderer>(true))
                if (renderer.sharedMesh != null && renderer.sharedMesh.vertexCount > 0 &&
                    renderer.sharedMaterials.Length > 0) return true;
            return false;
        }

        internal static Result Inspect(GameObject avatar, Camera camera)
        {
            var result = new Result { code = "avatar_missing", model = "" };
            if (avatar == null) return result;
            var vrm = avatar.GetComponentInChildren<Vrm10Instance>();
            var animator = avatar.GetComponentInChildren<Animator>();
            result.vrm = vrm != null;
            result.model = vrm != null && vrm.Vrm != null ? vrm.Vrm.Meta.Name : "";
            result.humanoid = animator != null && animator.avatar != null && animator.avatar.isValid && animator.avatar.isHuman;
            result.directCamera = camera != null && camera.enabled && camera.gameObject.activeInHierarchy && camera.targetTexture == null;
            if (camera == null) { result.code = "camera_missing"; return result; }
            result.cameraPosition = camera.transform.position; result.fieldOfView = camera.fieldOfView;
            Plane[] planes = GeometryUtility.CalculateFrustumPlanes(camera);
            Bounds bounds = default;
            foreach (SkinnedMeshRenderer renderer in avatar.GetComponentsInChildren<SkinnedMeshRenderer>())
            {
                Mesh mesh = renderer.sharedMesh;
                if (mesh == null || mesh.vertexCount == 0 || !renderer.enabled || !renderer.gameObject.activeInHierarchy) continue;
                if (result.renderers++ == 0) bounds = renderer.bounds; else bounds.Encapsulate(renderer.bounds);
                result.vertices += mesh.vertexCount;
                bool material = false;
                foreach (Material item in renderer.sharedMaterials)
                    if (item != null && item.shader != null && item.shader.isSupported && item.passCount > 0) material = true;
                if (material && renderer.isVisible && (camera.cullingMask & (1 << renderer.gameObject.layer)) != 0 &&
                    renderer.bounds.size.sqrMagnitude > .000001f && GeometryUtility.TestPlanesAABB(planes, renderer.bounds))
                    result.visibleRenderers++;
            }
            result.boundsCenter = bounds.center; result.boundsSize = bounds.size;
            result.ready = result.vrm && result.humanoid && result.directCamera && result.visibleRenderers > 0;
            result.code = result.ready ? "visible_geometry" : "geometry_not_visible";
            return result;
        }
    }
}
