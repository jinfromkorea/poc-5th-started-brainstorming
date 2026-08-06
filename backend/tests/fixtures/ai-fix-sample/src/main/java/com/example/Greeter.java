package com.example;

public class Greeter {
    public String greet(String name) {
        // BUG for the AI-fix integration test to find and fix: missing
        // semicolon + string concatenation typo (using a python-style '+'
        // with a stray character). Deliberately simple/obvious so the fix
        // is cheap and near-deterministic.
        return "Hello, " + name + "!"
    }
}
