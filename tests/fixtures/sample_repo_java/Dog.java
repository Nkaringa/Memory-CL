/* Dog — exercises extends + implements, constructor, constants, nested class. */
package com.example.zoo;

import java.util.List;
import java.util.*;
import static java.lang.Math.max;

/**
 * A loyal dog.
 */
public class Dog extends Pet implements Animal, Comparable<Dog> {
    public static final String SOUND = "woof";
    private static final int legCount = 4;
    private int age;

    /** Builds a dog of the given age. */
    public Dog(int age) {
        this.age = max(0, age);
    }

    /** What the dog says. */
    @Override
    public String speak() {
        return SOUND.toUpperCase();
    }

    @Override
    public int compareTo(Dog other) {
        return this.age - other.age;
    }

    static class Collar {
        void tighten() {}
    }
}
