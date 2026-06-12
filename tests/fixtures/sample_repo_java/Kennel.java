/* Kennel — exercises imports + cross-class instantiation. */
package com.example.zoo;

import java.util.ArrayList;
import java.util.List;

public class Kennel {
    private final List<Dog> dogs = new ArrayList<>();

    public Dog adopt(int age) {
        Dog dog = new Dog(age);
        dogs.add(dog);
        return dog;
    }
}
