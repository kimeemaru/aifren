using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using AIFren.UnityPoc.Avatar;
using AIFren.UnityPoc.UI;
using NUnit.Framework;
using UnityEngine;

namespace AIFren.UnityPoc.Tests.EditMode
{
    public sealed class AssetLibraryGridTests
    {
        [Test]
        public void ModelImportFormatCheckRejectsTextAndImageFiles()
        {
            string root = Path.Combine(Application.temporaryCachePath, "AIFrenAssetValidationTests");
            Directory.CreateDirectory(root);
            string valid = Path.Combine(root, Guid.NewGuid() + ".vrm");
            string image = Path.Combine(root, Guid.NewGuid() + ".jpg");
            string text = Path.Combine(root, Guid.NewGuid() + ".txt");
            WriteVrm0Glb(valid);
            File.WriteAllBytes(image, new byte[] { 0xff, 0xd8, 0xff, 0xe0 });
            File.WriteAllText(text, "not a model");
            MethodInfo validVrm = typeof(AvatarLoader).Assembly.GetType("AIFren.UnityPoc.Avatar.AvatarModelFormatClassifier")
                .GetMethod("IsSupportedAvatarFile", BindingFlags.Static | BindingFlags.NonPublic | BindingFlags.Public);
            try
            {
                Assert.IsTrue((bool)validVrm.Invoke(null, new object[] { valid }));
                Assert.IsFalse((bool)validVrm.Invoke(null, new object[] { image }));
                Assert.IsFalse((bool)validVrm.Invoke(null, new object[] { text }));
            }
            finally { File.Delete(valid); File.Delete(image); File.Delete(text); }
        }

        private static void WriteVrm0Glb(string path)
        {
            byte[] json = Encoding.UTF8.GetBytes("{\"asset\":{\"version\":\"2.0\"},\"extensions\":{\"VRM\":{}}}");
            int originalLength = json.Length;
            Array.Resize(ref json, (json.Length + 3) & ~3);
            for (int index = originalLength; index < json.Length; index++) json[index] = 0x20;
            using (var stream = File.Create(path))
            using (var writer = new BinaryWriter(stream))
            {
                writer.Write(0x46546C67u); writer.Write(2u); writer.Write((uint)(12 + 8 + json.Length + 8 + 4));
                writer.Write((uint)json.Length); writer.Write(0x4E4F534Au); writer.Write(json);
                writer.Write(4u); writer.Write(0x004E4942u); writer.Write(new byte[4]);
            }
        }

        [Test]
        public void RebuildClearRemovesEveryExistingTileBeforeNewTilesAreAdded()
        {
            GameObject content = new GameObject("Asset library test content", typeof(RectTransform));
            var oldTiles = new List<GameObject>();
            for (int index = 0; index < 7; index++)
            {
                GameObject tile = new GameObject("Old tile " + index, typeof(RectTransform));
                tile.transform.SetParent(content.transform, false);
                oldTiles.Add(tile);
            }

            MethodInfo clear = typeof(AIFrenPocController).GetMethod(
                "ClearLibraryTiles", BindingFlags.NonPublic | BindingFlags.Static);
            Assert.NotNull(clear);
            clear.Invoke(null, new object[] { content.transform });

            Assert.AreEqual(0, content.transform.childCount);
            foreach (GameObject tile in oldTiles) UnityEngine.Object.DestroyImmediate(tile);
            UnityEngine.Object.DestroyImmediate(content);
        }
    }
}
