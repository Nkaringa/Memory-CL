/* Animal contract — exercises interface + implicit constant in Java. */
package com.example.zoo;

/**
 * Behavior contract for animals.
 */
public interface Animal {
    int MAX_AGE = 100;

    String speak();
}
