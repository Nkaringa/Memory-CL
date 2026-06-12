// Geometry primitives for the sample repo.
// Exercises namespaces, bases, constants, doc comments, and local functions.

using System;
using System.Collections.Generic;
using static System.Math;
using Vec = System.Numerics.Vector2;

namespace Sample.Geometry
{
    /// <summary>A circle that can report its area.</summary>
    public class Circle : Shape, IMeasurable
    {
        public const double UNIT_RADIUS = 1.0;
        private const string label = "circle";
        public static readonly double GOLDEN = 1.618;
        public static readonly double defaultRadius = 2.0;

        private double _radius;

        public Circle(double radius)
        {
            _radius = radius;
        }

        /// <summary>Computes the area.</summary>
        /// <returns>area in square units</returns>
        public double Area()
        {
            double Square(double x) { return x * x; }
            return PI * Square(_radius);
        }

        public double Diameter => _radius * 2;

        public double Radius
        {
            get { return _radius; }
            set { _radius = Clamp(value); }
        }

        public string Label { get; set; } = "circle";

        private static double Clamp(double v) => Max(v, 0.0);

        private class Cache
        {
            public void Clear() { }
        }
    }
}
