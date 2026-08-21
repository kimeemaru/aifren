using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using UnityEngine;

namespace AIFren.UnityPoc.Avatar
{
    [Serializable] internal sealed class ManagedAssetRecord { public string id; public string kind; public string path; public string displayName; public string thumbnailPath; }
    [Serializable] internal sealed class ManagedAssetIndex { public List<ManagedAssetRecord> assets = new List<ManagedAssetRecord>(); }

    internal sealed class ManagedAssetLibrary
    {
        internal const string ModelKind = "model";
        internal const string BackgroundKind = "background";
        private const string ModelsDirectory = "Models";
        private const string BackgroundsDirectory = "Backgrounds";
        private const string ThumbnailsDirectory = "Thumbnails";
        private readonly string root;
        private readonly string indexPath;
        private ManagedAssetIndex index;
        internal int DuplicateRecordsRepaired { get; private set; }
        internal static ManagedAssetLibrary Load() => new ManagedAssetLibrary();
        private ManagedAssetLibrary() : this(Path.Combine(Application.persistentDataPath, "AIFren", "AssetLibrary")) { }
        internal static ManagedAssetLibrary CreateForTesting(string storageRoot) => new ManagedAssetLibrary(storageRoot);
        private ManagedAssetLibrary(string storageRoot)
        {
            root = storageRoot;
            indexPath = Path.Combine(root, "library.json"); Directory.CreateDirectory(root);
            try { index = File.Exists(indexPath) ? JsonUtility.FromJson<ManagedAssetIndex>(File.ReadAllText(indexPath)) : new ManagedAssetIndex(); }
            catch { index = new ManagedAssetIndex(); }
            if (index == null || index.assets == null) index = new ManagedAssetIndex();
            DeduplicateIndex();
            RepairUnsafeRecords();
            RemoveInvalidModelRecords();
            RepairFriendlyBackgroundNames();
        }
        internal List<ManagedAssetRecord> Assets(string kind)
        {
            var unique = new List<ManagedAssetRecord>();
            var ids = new HashSet<string>(StringComparer.Ordinal);
            foreach (ManagedAssetRecord record in index.assets)
                if (record.kind == kind &&
                    TryGetSafeManagedFilePath(kind, record.path, false, out _, out _) &&
                    File.Exists(record.path) && ids.Add(record.id)) unique.Add(record);
            return unique;
        }
        internal List<ManagedAssetRecord> Records(string kind) => index.assets.FindAll(record => record.kind == kind);
        internal string ThumbnailPath(string id) => Path.Combine(root, "Thumbnails", id + ".png");
        internal void SetThumbnailPath(string id, string path)
        {
            ManagedAssetRecord record = index.assets.Find(x => x.id == id);
            if (record == null) return;
            record.thumbnailPath = path ?? string.Empty; Save();
        }
        internal bool TryImport(string source, string kind, out ManagedAssetRecord record, out string error)
        {
            record = null; error = string.Empty;
            try {
                if (kind == ModelKind && !IsValidVrmFile(source))
                {
                    error = "Choose a valid .vrm avatar file.";
                    return false;
                }
                byte[] bytes = File.ReadAllBytes(source); string ext = Path.GetExtension(source).ToLowerInvariant();
                using (var sha = SHA256.Create()) { string id = BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
                    record = index.assets.Find(x => x.id == id && x.kind == kind);
                    if (record != null && File.Exists(record.path))
                    {
                        if (kind == BackgroundKind && IsMissingOrHashLikeName(record.displayName))
                        {
                            record.displayName = FriendlySourceName(source, "Imported background");
                            Save();
                        }
                        return true;
                    }
                    string folder = Path.Combine(root, AssetDirectoryName(kind)); Directory.CreateDirectory(folder);
                    string path = Path.Combine(folder, id + ext); if (!File.Exists(path)) File.WriteAllBytes(path, bytes);
                    if (record == null)
                    {
                        record = new ManagedAssetRecord { id=id, kind=kind, path=path, displayName=FriendlySourceName(source, kind == BackgroundKind ? "Imported background" : "Imported model"), thumbnailPath=kind == BackgroundKind ? path : ThumbnailPath(id) };
                        index.assets.Add(record);
                    }
                    else
                    {
                        // Repair a stale record in place instead of appending a
                        // second entry for identical content.
                        record.path = path;
                        if (string.IsNullOrWhiteSpace(record.displayName) || (kind == BackgroundKind && IsMissingOrHashLikeName(record.displayName))) record.displayName = FriendlySourceName(source, kind == BackgroundKind ? "Imported background" : "Imported model");
                        if (kind == BackgroundKind && string.IsNullOrWhiteSpace(record.thumbnailPath)) record.thumbnailPath = path;
                    }
                    Save(); return true;
                }
            } catch(Exception e) { error=e.Message; return false; }
        }
        internal bool Delete(string kind, IEnumerable<string> ids)
        {
            bool changed=false;
            foreach(var id in new List<string>(ids))
            {
                var record=index.assets.Find(x=>x.id==id && x.kind==kind);
                if(record==null) continue;
                // All filesystem operations are explicitly kind/directory
                // constrained.  A bad library.json record is removed below,
                // but never grants authority to delete its referenced file.
                DeleteManagedFile(kind, record.path, false, record.id);
                DeleteManagedFile(kind, record.thumbnailPath, true, record.id);
                DeleteManagedFile(kind, ThumbnailPath(record.id), true, record.id);
                DeleteManagedFile(kind, VrmThumbnailGenerator.VersionMarkerPath(ThumbnailPath(record.id)), true, record.id);
                // A missing/corrupt file must not keep a stale library record.
                index.assets.Remove(record);
                changed=true;
            }
            if(changed) Save(); return changed;
        }
        internal List<ManagedAssetRecord> RemoveInvalidModelRecords()
        {
            var removed = new List<ManagedAssetRecord>();
            foreach (ManagedAssetRecord record in new List<ManagedAssetRecord>(index.assets))
            {
                if (record.kind != ModelKind || IsValidVrmFile(record.path)) continue;
                if (Delete(ModelKind, new[] { record.id })) removed.Add(record);
            }
            return removed;
        }
        internal void SetDisplayName(string id, string value)
        {
            ManagedAssetRecord record = index.assets.Find(x => x.id == id);
            if (record == null || string.IsNullOrWhiteSpace(value)) return;
            record.displayName = value.Trim(); Save();
        }
        private void DeduplicateIndex()
        {
            var unique = new List<ManagedAssetRecord>();
            var byKey = new Dictionary<string, ManagedAssetRecord>(StringComparer.Ordinal);
            foreach (ManagedAssetRecord record in index.assets)
            {
                if (record == null || string.IsNullOrWhiteSpace(record.id) || string.IsNullOrWhiteSpace(record.kind)) continue;
                string key = record.kind + "\n" + record.id;
                if (!byKey.TryGetValue(key, out ManagedAssetRecord canonical))
                {
                    byKey[key] = record; unique.Add(record); continue;
                }
                DuplicateRecordsRepaired++;
                // Merge useful metadata into the retained entry. Do not delete
                // any duplicate file here: an index repair must never risk the
                // one working managed asset.
                if (!File.Exists(canonical.path) && File.Exists(record.path)) canonical.path = record.path;
                if (string.IsNullOrWhiteSpace(canonical.displayName) && !string.IsNullOrWhiteSpace(record.displayName)) canonical.displayName = record.displayName;
                if ((!File.Exists(canonical.thumbnailPath)) && File.Exists(record.thumbnailPath)) canonical.thumbnailPath = record.thumbnailPath;
            }
            if (DuplicateRecordsRepaired == 0) return;
            index.assets = unique;
            Save();
            Debug.Log("AIFren asset library repaired " + DuplicateRecordsRepaired + " duplicate metadata record(s).");
        }
        internal static bool IsValidVrmFile(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || !string.Equals(Path.GetExtension(path), ".vrm", StringComparison.OrdinalIgnoreCase) || !File.Exists(path)) return false;
            try
            {
                var info = new FileInfo(path);
                if (info.Length < 20 || info.Length > uint.MaxValue) return false;
                using (var reader = new BinaryReader(File.OpenRead(path)))
                {
                    uint magic = reader.ReadUInt32(); uint version = reader.ReadUInt32(); uint declaredLength = reader.ReadUInt32();
                    return magic == 0x46546C67 && version == 2 && declaredLength == info.Length;
                }
            }
            catch { return false; }
        }
        private void RepairUnsafeRecords()
        {
            bool changed = false;
            foreach (ManagedAssetRecord record in new List<ManagedAssetRecord>(index.assets))
            {
                string reason = "missing or malformed record";
                if (record == null ||
                    (record.kind != ModelKind && record.kind != BackgroundKind) ||
                    !TryGetSafeManagedFilePath(record.kind, record.path, false, out _, out reason))
                {
                    index.assets.Remove(record);
                    changed = true;
                    Debug.LogWarning("AIFren asset library removed unsafe metadata record " +
                        (record != null ? record.id : "<null>") + ": " + reason);
                    continue;
                }
                if (record.kind == ModelKind && !string.IsNullOrWhiteSpace(record.thumbnailPath) &&
                    !TryGetSafeManagedFilePath(record.kind, record.thumbnailPath, true, out _, out reason))
                {
                    // Keep a safe model usable; only discard the untrusted
                    // thumbnail reference.  The generic tile remains valid.
                    record.thumbnailPath = ThumbnailPath(record.id);
                    changed = true;
                    Debug.LogWarning("AIFren asset library cleared unsafe thumbnail metadata for " + record.id + ": " + reason);
                }
            }
            if (changed) Save();
        }

        private void RepairFriendlyBackgroundNames()
        {
            bool changed = false;
            foreach (ManagedAssetRecord record in index.assets)
            {
                if (record == null || record.kind != BackgroundKind || !IsMissingOrHashLikeName(record.displayName)) continue;
                // Older managed files only retain their content-hash filename;
                // never show that internal implementation detail to users.
                record.displayName = "Imported background";
                changed = true;
            }
            if (changed) Save();
        }

        private static string FriendlySourceName(string source, string fallback)
        {
            string name = Path.GetFileNameWithoutExtension(source);
            return string.IsNullOrWhiteSpace(name) || IsContentHash(name) ? fallback : name.Trim();
        }

        private static bool IsMissingOrHashLikeName(string value)
        {
            return string.IsNullOrWhiteSpace(value) || IsContentHash(value.Trim());
        }

        private static bool IsContentHash(string value)
        {
            if (value == null || value.Length != 64) return false;
            foreach (char character in value)
                if (!((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f') || (character >= 'A' && character <= 'F')))
                    return false;
            return true;
        }

        private void DeleteManagedFile(string kind, string rawPath, bool thumbnail, string assetId)
        {
            if (!TryGetSafeManagedFilePath(kind, rawPath, thumbnail, out string path, out string reason))
            {
                if (!string.IsNullOrWhiteSpace(rawPath))
                    Debug.LogWarning("AIFren refused unsafe " + (thumbnail ? "thumbnail" : "asset") +
                        " deletion for " + assetId + ": " + reason);
                return;
            }
            try
            {
                // Directories and symlinks are rejected by validation. File.Delete
                // is intentionally the only deletion operation used here.
                if (Directory.Exists(path))
                {
                    Debug.LogWarning("AIFren refused directory deletion for " + assetId + ": " + path);
                    return;
                }
                if (File.Exists(path)) File.Delete(path);
            }
            catch (Exception error)
            {
                Debug.LogWarning("Could not remove managed asset file " + path + ": " + error.Message);
            }
        }

        private bool TryGetSafeManagedFilePath(string kind, string rawPath, bool thumbnail, out string canonicalPath, out string reason)
        {
            canonicalPath = string.Empty;
            reason = string.Empty;
            if (string.IsNullOrWhiteSpace(rawPath)) { reason = "path is empty"; return false; }
            if (kind != ModelKind && kind != BackgroundKind) { reason = "unknown asset kind"; return false; }
            try
            {
                string expectedDirectory = Path.GetFullPath(Path.Combine(root, thumbnail ? ThumbnailsDirectory : AssetDirectoryName(kind)));
                string candidate = Path.GetFullPath(rawPath);
                if (!IsStrictChildOf(candidate, expectedDirectory))
                {
                    reason = "path is outside its managed " + (thumbnail ? "thumbnail" : "asset") + " directory";
                    return false;
                }
                // Reject a symlink/reparse point anywhere under the managed
                // directory. This never follows a link to delete an external
                // target, including a link hidden in a subdirectory.
                if (ContainsReparsePoint(expectedDirectory, candidate))
                {
                    reason = "path contains a symbolic-link or reparse-point component";
                    return false;
                }
                if (Directory.Exists(candidate))
                {
                    reason = "path resolves to a directory";
                    return false;
                }
                canonicalPath = candidate;
                return true;
            }
            catch (Exception error)
            {
                reason = "malformed path: " + error.Message;
                return false;
            }
        }

        private static string AssetDirectoryName(string kind)
        {
            return kind == ModelKind ? ModelsDirectory : BackgroundsDirectory;
        }

        // Segment-aware containment: /AssetLibrary2 never matches
        // /AssetLibrary, and normalising ../ first cannot escape the directory.
        private static bool IsStrictChildOf(string candidate, string directory)
        {
            string normalizedDirectory = Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            string prefix = normalizedDirectory + Path.DirectorySeparatorChar;
            return candidate.StartsWith(prefix, PathComparison);
        }

        private static StringComparison PathComparison =>
            Path.DirectorySeparatorChar == '\\' ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;

        private static bool ContainsReparsePoint(string expectedDirectory, string candidate)
        {
            // The expected directory itself is included so a Models/ or
            // Thumbnails/ symlink is refused as well as a linked child.
            string current = Path.GetFullPath(expectedDirectory);
            if (HasReparsePoint(current)) return true;
            string relative = candidate.Substring((current + Path.DirectorySeparatorChar).Length);
            foreach (string segment in relative.Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar))
            {
                if (string.IsNullOrEmpty(segment)) continue;
                current = Path.Combine(current, segment);
                if (HasReparsePoint(current)) return true;
            }
            return false;
        }

        private static bool HasReparsePoint(string path)
        {
            try { return (File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0; }
            catch (FileNotFoundException) { return false; }
            catch (DirectoryNotFoundException) { return false; }
            catch { return true; } // Unknown filesystem state is not safe to delete.
        }
        private void Save() { File.WriteAllText(indexPath, JsonUtility.ToJson(index, true)); }
    }
}
