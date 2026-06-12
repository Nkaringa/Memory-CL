namespace Sample.Models;

/// <summary>Anything with a measurable area.</summary>
public interface IMeasurable
{
    double Area();
}

/// <summary>Immutable point record.</summary>
public record Point(double X, double Y);

public record NamedPoint(string Name, double X, double Y) : Point(X, Y);

public enum ShapeKind
{
    Circle,
    Square,
}
