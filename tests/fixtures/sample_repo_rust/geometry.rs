//! Geometry primitives — exercises structs, traits, impls and consts.

use std::fmt::{self, Display};

/// Scale factor applied to every shape.
pub const SCALE: f64 = 2.0;

/// A 2D point.
pub struct Point {
    x: f64,
    y: f64,
}

/// Something with a measurable area.
pub trait Area {
    fn area(&self) -> f64;

    /// Default human-readable description.
    fn describe(&self) -> String {
        String::from("shape")
    }
}

impl Point {
    /// Builds a point at the origin.
    pub fn origin() -> Self {
        Point { x: 0.0, y: 0.0 }
    }

    fn norm(&self) -> f64 {
        (self.x * self.x + self.y * self.y).sqrt()
    }
}

impl Area for Point {
    fn area(&self) -> f64 {
        self.norm() * SCALE
    }
}

impl Display for Point {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "({}, {})", self.x, self.y)
    }
}
