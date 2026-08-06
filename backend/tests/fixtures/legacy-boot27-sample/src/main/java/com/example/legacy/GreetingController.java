package com.example.legacy;

import javax.servlet.http.HttpServletRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

// Deliberately uses the javax.servlet.* namespace (removed in Spring Boot 3,
// which moved to Jakarta EE / jakarta.servlet.*) -- this is the single most
// common real migration pain point UpgradeSpringBoot_3_0 is meant to handle,
// so it's a genuine test of whether the recipe does meaningful work rather
// than a no-op.
@RestController
public class GreetingController {

    @GetMapping("/greet")
    public String greet(HttpServletRequest request) {
        return "Hello from " + request.getRemoteAddr();
    }
}
